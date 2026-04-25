"""
blackboard/client.py — Core HTTP client for CDU Learnline.

Responsibilities:
  • Hold an httpx.AsyncClient with session cookies injected
  • Try Blackboard's public REST API first (/learn/api/public/v1/…)
  • Fall back to HTML scraping with BeautifulSoup when REST fails
  • Detect session expiry (401/redirect to login) and automatically re-authenticate
  • Expose high-level async methods for each data domain

Usage:
    client = BlackboardClient()
    await client.initialize()
    courses = await client.get_courses()
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from blackboard import auth
from blackboard.models import (
    Announcement,
    Assignment,
    ContentItem,
    Course,
    GradeEntry,
    UserProfile,
)
from config import settings

# ──────────────────────────────────────────────
#  Constants
# ──────────────────────────────────────────────

API_BASE = "/learn/api/public/v1"
DEFAULT_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


# ──────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────

def _parse_bb_datetime(value: str | None) -> datetime | None:
    """Parse an ISO-8601 datetime string from Blackboard into a UTC datetime."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _html_to_text(html: str) -> str:
    """Strip HTML tags and normalise whitespace."""
    if not html:
        return ""
    text = BeautifulSoup(html, "html.parser").get_text(separator=" ")
    return re.sub(r"\s+", " ", text).strip()


def _course_url(course_id: str) -> str:
    # Blackboard Ultra deep-link format
    return f"{settings.base_url}/ultra/courses/{course_id}/cl/outline"


# ──────────────────────────────────────────────
#  BlackboardClient
# ──────────────────────────────────────────────

class BlackboardClient:
    """
    Async HTTP client wrapping CDU Learnline's REST API and web interface.

    Call `await client.initialize()` before using any data methods.
    The client auto-refreshes the session when it detects expiry.
    """

    def __init__(self) -> None:
        self._cookies: dict[str, str] = {}
        self._http: httpx.AsyncClient | None = None
        self._user: UserProfile | None = None

    # ── Lifecycle ──────────────────────────────

    async def initialize(self) -> None:
        """Load session cookies and create the HTTP client."""
        self._cookies = await auth.get_cookies()
        self._build_client()
        # Validate session; re-login if stale
        if not await self._check_session():
            print("[client] Session invalid, re-authenticating …", file=sys.stderr)
            self._cookies = await auth.get_cookies(force_refresh=True)
            self._build_client()

    async def close(self) -> None:
        if self._http:
            await self._http.aclose()

    def _build_client(self) -> None:
        # Replace the old client (it will be GC'd; connections are short-lived)
        self._http = httpx.AsyncClient(
            base_url=settings.base_url,
            cookies=self._cookies,
            headers=DEFAULT_HEADERS,
            follow_redirects=True,
            timeout=30.0,
        )

    # ── Internal request helpers ───────────────

    async def _check_session(self) -> bool:
        """Return True if our session cookies are still valid."""
        try:
            resp = await self._http.get("/ultra/institution-page")
            final_url = str(resp.url)
            base_host = settings.base_url.split("//")[-1].split("/")[0]
            # Session is valid if we stayed on our Blackboard domain (not redirected to SSO)
            if base_host in final_url:
                login_keywords = ["login", "signin", "saml", "auth", "shibboleth", "/cas/"]
                if not any(kw in final_url.lower() for kw in login_keywords):
                    return True
            # Classic Blackboard fallback — try the portal page
            if resp.status_code == 200 and "text/html" in resp.headers.get("content-type", ""):
                return True
            return False
        except Exception:
            return False

    async def _api_get(self, path: str, params: dict | None = None) -> dict | list | None:
        """
        GET from the Blackboard REST API.
        Returns parsed JSON dict/list, or None on failure.
        Auto-retries once after re-authentication on 401/403.
        """
        for attempt in range(2):
            try:
                resp = await self._http.get(f"{API_BASE}{path}", params=params or {})
                if resp.status_code in (401, 403) and attempt == 0:
                    print("[client] Session expired, re-authenticating …", file=sys.stderr)
                    self._cookies = await auth.get_cookies(force_refresh=True)
                    self._build_client()
                    continue
                if resp.status_code == 200:
                    ct = resp.headers.get("content-type", "")
                    if "application/json" in ct:
                        return resp.json()
                return None
            except Exception as exc:
                print(f"[client] API error on {path}: {exc}", file=sys.stderr)
                return None

    async def _web_get(self, path: str, params: dict | None = None) -> BeautifulSoup | None:
        """
        GET a web page and return a BeautifulSoup object.
        Used as fallback when REST API is unavailable.
        """
        try:
            resp = await self._http.get(path, params=params or {})
            if resp.status_code == 200:
                return BeautifulSoup(resp.text, "html.parser")
        except Exception as exc:
            print(f"[client] Web fetch error on {path}: {exc}", file=sys.stderr)
        return None

    # ─────────────────────────────────────────────────────────────
    #  Public data methods
    # ─────────────────────────────────────────────────────────────

    # ── User profile ────────────────────────────

    async def get_user_profile(self) -> UserProfile | None:
        """Fetch the currently logged-in student's profile."""
        if self._user:
            return self._user

        data = await self._api_get("/users/me")
        if data:
            name = data.get("name", {})
            self._user = UserProfile(
                id=data.get("id", ""),
                username=data.get("userName", ""),
                given_name=name.get("given", ""),
                family_name=name.get("family", ""),
                email=data.get("contact", {}).get("email"),
                student_id=data.get("studentId"),
            )
            return self._user

        # Fallback: parse from the web page
        soup = await self._web_get("/webapps/portal/frameset.jsp")
        if soup:
            # Blackboard often puts the user's name in the header
            name_elem = soup.select_one("#global-nav-user-display-name, .user-display-name, #topframe")
            if name_elem:
                full_name = name_elem.get_text(strip=True)
                parts = full_name.split(" ", 1)
                self._user = UserProfile(
                    id="unknown",
                    username="unknown",
                    given_name=parts[0] if parts else full_name,
                    family_name=parts[1] if len(parts) > 1 else "",
                    student_id=None,
                )
                return self._user

        return None

    # ── Courses ─────────────────────────────────

    async def get_courses(self) -> list[Course]:
        """List courses the student is actually enrolled in (Student role only)."""
        profile = await self.get_user_profile()
        if not profile:
            return await self._scrape_courses()

        uid = profile.id

        # Use the memberships endpoint — returns only courses this user is enrolled in
        memberships = await self._api_get(f"/users/{uid}/courses", params={"limit": 100})
        if not memberships or "results" not in memberships:
            return await self._scrape_courses()

        # Filter to Student-role memberships only
        course_ids = [
            m["courseId"]
            for m in memberships["results"]
            if m.get("courseRoleId") in ("Student", "student", None)
        ]

        if not course_ids:
            # No student role filter — take all memberships
            course_ids = [m["courseId"] for m in memberships["results"]]

        # Fetch each course's details concurrently
        async def fetch_one(cid: str) -> Course | None:
            data = await self._api_get(f"/courses/{cid}")
            if not data:
                return None
            return Course(
                id=data.get("id", cid),
                course_id=data.get("courseId", cid),
                name=data.get("name", cid),
                term=(
                    data["term"]["name"]
                    if isinstance(data.get("term"), dict) and data["term"].get("name")
                    else None
                ),
                is_available=(
                    data.get("availability", {}).get("available", "Yes") == "Yes"
                ),
                description=_html_to_text(data.get("description", "")),
                url=_course_url(data.get("id", cid)),
            )

        import asyncio as _asyncio
        results = await _asyncio.gather(*[fetch_one(cid) for cid in course_ids])
        return [c for c in results if c is not None]

    async def _scrape_courses(self) -> list[Course]:
        """Scrape enrolled courses from the Blackboard web interface."""
        soup = await self._web_get("/webapps/portal/frameset.jsp")
        if not soup:
            return []

        courses = []
        # Blackboard typically lists courses in a module with id containing "_23_1" or similar
        for link in soup.select('a[href*="/webapps/blackboard/execute/launcher?type=Course"]'):
            href = link.get("href", "")
            name = link.get_text(strip=True)
            # Extract course ID from URL
            match = re.search(r"id=(_\d+_\d+)", href)
            course_id = match.group(1) if match else href
            if name and course_id:
                courses.append(Course(
                    id=course_id,
                    course_id=course_id,
                    name=name,
                    url=urljoin(settings.base_url, href),
                ))
        return courses

    # ── Announcements ────────────────────────────

    async def get_announcements(self, course_id: str, course_name: str, limit: int = 10) -> list[Announcement]:
        """Fetch announcements for a specific course."""
        data = await self._api_get(
            f"/courses/{course_id}/announcements",
            params={"limit": limit, "sort": "created", "order": "desc"},
        )

        if data and "results" in data:
            announcements = []
            for item in data["results"]:
                announcements.append(Announcement(
                    id=item.get("id", ""),
                    course_id=course_id,
                    course_name=course_name,
                    title=item.get("title", ""),
                    body=_html_to_text(item.get("body", "")),
                    creator=item.get("creator", {}).get("name", {}).get("given", "") if isinstance(item.get("creator"), dict) else None,
                    created=_parse_bb_datetime(item.get("created")),
                    modified=_parse_bb_datetime(item.get("modified")),
                ))
            return announcements

        # Fallback: scrape announcements page
        return await self._scrape_announcements(course_id, course_name)

    async def _scrape_announcements(self, course_id: str, course_name: str) -> list[Announcement]:
        """Scrape announcements from the course web page."""
        soup = await self._web_get(
            "/webapps/blackboard/execute/announcement",
            params={"method": "search", "context": "course", "course_id": course_id},
        )
        if not soup:
            return []

        announcements = []
        for row in soup.select(".announcementRow, .announcementItem, [id^='announcement']"):
            title_elem = row.select_one("h3, h4, .title, strong")
            body_elem = row.select_one(".details, .body, p")
            if title_elem:
                announcements.append(Announcement(
                    id=row.get("id", ""),
                    course_id=course_id,
                    course_name=course_name,
                    title=title_elem.get_text(strip=True),
                    body=_html_to_text(str(body_elem)) if body_elem else "",
                ))
        return announcements

    # ── Assignments ──────────────────────────────

    async def get_assignments(self, course_id: str, course_name: str) -> list[Assignment]:
        """Fetch assignments and assessments for a course."""
        data = await self._api_get(
            f"/courses/{course_id}/contents",
            params={"contentHandler.id": "resource/x-bb-assignment", "limit": 100},
        )

        assignments = []
        if data and "results" in data:
            for item in data["results"]:
                due_raw = item.get("availability", {}).get("adaptiveRelease", {})
                due_date = _parse_bb_datetime(item.get("grading", {}).get("due") if isinstance(item.get("grading"), dict) else None)

                assignments.append(Assignment(
                    id=item.get("id", ""),
                    course_id=course_id,
                    course_name=course_name,
                    title=item.get("title", "Untitled"),
                    due_date=due_date,
                    description=_html_to_text(item.get("body", "")),
                    url=urljoin(settings.base_url, item.get("contentHandler", {}).get("url", "")) if isinstance(item.get("contentHandler"), dict) else None,
                ))

        if not assignments:
            # Try gradebook columns as a source of assessment names + due dates
            assignments = await self._assignments_from_gradebook(course_id, course_name)

        return assignments

    async def _assignments_from_gradebook(self, course_id: str, course_name: str) -> list[Assignment]:
        """Derive assignments from gradebook columns (which have due dates)."""
        data = await self._api_get(
            f"/courses/{course_id}/gradebook/columns",
            params={"limit": 100},
        )
        if not data or "results" not in data:
            return []

        assignments = []
        for col in data["results"]:
            if col.get("contentId"):  # linked to a content item
                assignments.append(Assignment(
                    id=col.get("id", ""),
                    course_id=course_id,
                    course_name=course_name,
                    title=col.get("name", ""),
                    due_date=_parse_bb_datetime(col.get("due")),
                    max_score=col.get("points"),
                ))
        return assignments

    # ── Grades ───────────────────────────────────

    async def get_grades(self, course_id: str) -> list[GradeEntry]:
        """Fetch gradebook entries for the student in a course."""
        profile = await self.get_user_profile()
        if not profile or profile.id == "unknown":
            return await self._scrape_grades(course_id)

        data = await self._api_get(f"/courses/{course_id}/gradebook/users/{profile.id}")

        if data and "results" in data:
            grades = []
            for item in data["results"]:
                col = item.get("columnId", "")
                grades.append(GradeEntry(
                    column_name=item.get("columnName", col),
                    score=item.get("score"),
                    max_score=item.get("possible"),
                    status=item.get("status"),
                    feedback=_html_to_text(item.get("feedback", "")),
                ))
            return grades

        return await self._scrape_grades(course_id)

    async def _scrape_grades(self, course_id: str) -> list[GradeEntry]:
        """Scrape grades from Blackboard's My Grades page."""
        soup = await self._web_get(
            "/webapps/bb-mygrades-LEARN/myGrades",
            params={"course_id": course_id, "stream_name": "mygrades"},
        )
        if not soup:
            return []

        grades = []
        for row in soup.select("tr.gradable-row, .gradeRow, [id^='grades_table_row']"):
            name_elem = row.select_one(".cell.gradable a, .title a, td:first-child")
            score_elem = row.select_one(".cell.grade, .score, .grade")
            if name_elem:
                name = name_elem.get_text(strip=True)
                score_text = score_elem.get_text(strip=True) if score_elem else ""
                # Try to parse "85 / 100" or just "85"
                score, max_score = None, None
                match = re.match(r"([\d.]+)\s*/\s*([\d.]+)", score_text)
                if match:
                    score = float(match.group(1))
                    max_score = float(match.group(2))
                elif re.match(r"[\d.]+", score_text):
                    score = float(re.match(r"[\d.]+", score_text).group())

                if name:
                    grades.append(GradeEntry(
                        column_name=name,
                        score=score,
                        max_score=max_score,
                        status="graded" if score is not None else "pending",
                    ))
        return grades

    # ── Course content ────────────────────────────

    async def get_course_content(self, course_id: str, folder_id: str | None = None) -> list[ContentItem]:
        """Fetch course content (folders, files, links, assignments)."""
        path = f"/courses/{course_id}/contents"
        if folder_id:
            path = f"/courses/{course_id}/contents/{folder_id}/children"

        data = await self._api_get(path, params={"limit": 100})

        if data and "results" in data:
            items = []
            for item in data["results"]:
                handler = item.get("contentHandler", {})
                handler_id = handler.get("id", "") if isinstance(handler, dict) else ""
                content_type = _map_handler_to_type(handler_id)
                items.append(ContentItem(
                    id=item.get("id", ""),
                    title=item.get("title", "Untitled"),
                    content_type=content_type,
                    description=_html_to_text(item.get("body", "")),
                    url=urljoin(settings.base_url, handler.get("url", "")) if isinstance(handler, dict) and handler.get("url") else None,
                ))
            return items

        return []


def _map_handler_to_type(handler_id: str) -> str:
    """Map a Blackboard contentHandler ID to a human-readable type."""
    mapping = {
        "resource/x-bb-assignment": "assignment",
        "resource/x-bb-document": "document",
        "resource/x-bb-folder": "folder",
        "resource/x-bb-externallink": "link",
        "resource/x-bb-file": "file",
        "resource/x-bb-video": "video",
        "resource/x-bb-forum": "discussion",
        "resource/x-bb-blankpage": "page",
    }
    for key, value in mapping.items():
        if key in handler_id:
            return value
    return "item"
