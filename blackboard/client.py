"""
blackboard/client.py — Core HTTP client for Blackboard MCP.
Works with any university running Blackboard Learn (Ultra or Classic).

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

import asyncio
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
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

# Cap concurrent API requests to avoid triggering Blackboard rate limits
_REQUEST_SEMAPHORE = asyncio.Semaphore(5)


# ──────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────

def _parse_bb_datetime(value: str | None) -> datetime | None:
    """Parse an ISO-8601 datetime string from Blackboard into a UTC-aware datetime."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # Ensure timezone-aware — some Blackboard instances omit the offset
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _html_to_text(html: str) -> str:
    """Strip HTML tags and normalise whitespace."""
    if not html:
        return ""
    text = BeautifulSoup(html, "html.parser").get_text(separator=" ")
    return re.sub(r"\s+", " ", text).strip()


def _course_url(course_id: str) -> str:
    """Build a direct link to the course, respecting the detected interface."""
    if getattr(settings, "interface", "ultra").lower() == "classic":
        return f"{settings.base_url}/webapps/blackboard/execute/launcher?type=Course&id={course_id}"
    return f"{settings.base_url}/ultra/courses/{course_id}/cl/outline"


# ──────────────────────────────────────────────
#  BlackboardClient
# ──────────────────────────────────────────────

class BlackboardClient:
    """
    Async HTTP client wrapping Blackboard's REST API and web interface.

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
        cached = auth.load_cached_cookies()
        if cached:
            self._cookies = cached
            await self._build_client()
        else:
            try:
                self._cookies = await auth.get_cookies()
                await self._build_client()
            except Exception as exc:
                print(f"[client] Could not load cookies: {exc}", file=sys.stderr)
                self._cookies = {}
                await self._build_client()

        if self._cookies and not await self._check_session():
            print("[client] Session check failed with loaded cookies, checking disk cache...", file=sys.stderr)
            cached_disk = auth.load_cached_cookies()
            if cached_disk and cached_disk != self._cookies:
                self._cookies = cached_disk
                await self._build_client()

    async def close(self) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    async def _build_client(self) -> None:
        """Close any existing HTTP client before creating a new one."""
        if self._http:
            try:
                await self._http.aclose()
            except Exception:
                pass
        headers = dict(DEFAULT_HEADERS)
        bb_router = self._cookies.get("BbRouter", "")
        xsrf_match = re.search(r"xsrf:([a-zA-Z0-9\-]+)", bb_router)
        if xsrf_match:
            headers["x-blackboard-xsrf"] = xsrf_match.group(1)
        elif "XSRF-TOKEN" in self._cookies:
            headers["x-blackboard-xsrf"] = self._cookies["XSRF-TOKEN"]

        self._http = httpx.AsyncClient(
            base_url=settings.base_url,
            cookies=self._cookies,
            headers=headers,
            follow_redirects=True,
            timeout=30.0,
        )

    # ── Internal request helpers ───────────────

    async def _check_session(self) -> bool:
        """Return True if our session cookies are still valid."""
        try:
            resp = await self._http.get(f"{API_BASE}/users/me")
            if resp.status_code == 200:
                return True
            resp = await self._http.get("/ultra/institution-page")
            final_url = str(resp.url)
            base_host = settings.base_url.split("//")[-1].split("/")[0]
            if base_host in final_url:
                login_keywords = ["login", "signin", "saml", "auth", "shibboleth", "/cas/"]
                if not any(kw in final_url.lower() for kw in login_keywords):
                    return True
            return False
        except Exception:
            return False

    async def _api_get(self, path: str, params: dict | None = None) -> dict | list | None:
        """
        GET from the Blackboard REST API.
        Returns parsed JSON or None on failure.
        Auto-retries once after re-authentication on 401.
        Respects the shared request semaphore to avoid rate limiting.
        """
        for attempt in range(2):
            try:
                async with _REQUEST_SEMAPHORE:
                    resp = await self._http.get(f"{API_BASE}{path}", params=params or {})
                if resp.status_code == 401 and attempt == 0:
                    disk_cookies = auth.load_cached_cookies()
                    if disk_cookies and disk_cookies != self._cookies:
                        self._cookies = disk_cookies
                        await self._build_client()
                        continue
                    if not await self._check_session():
                        print("[client] Session expired, re-authenticating...", file=sys.stderr)
                        try:
                            self._cookies = await auth.get_cookies(force_refresh=True)
                            await self._build_client()
                        except Exception:
                            pass
                        continue
                if resp.status_code == 200:
                    if "application/json" in resp.headers.get("content-type", ""):
                        return resp.json()
                return None
            except Exception as exc:
                if os.environ.get("BB_MCP_DEBUG"):
                    print(f"[client] API error on {path}: {exc}", file=sys.stderr)
                return None

    async def _web_get(self, path: str, params: dict | None = None) -> BeautifulSoup | None:
        """GET a web page and return a BeautifulSoup. Used as REST fallback."""
        try:
            async with _REQUEST_SEMAPHORE:
                resp = await self._http.get(path, params=params or {})
            if resp.status_code == 200:
                return BeautifulSoup(resp.text, "html.parser")
        except Exception as exc:
            if os.environ.get("BB_MCP_DEBUG"):
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
        if data and isinstance(data, dict):
            name = data.get("name") or {}
            contact = data.get("contact") or {}
            self._user = UserProfile(
                id=data.get("id", ""),
                username=data.get("userName", ""),
                given_name=name.get("given", "") if isinstance(name, dict) else "",
                family_name=name.get("family", "") if isinstance(name, dict) else "",
                email=contact.get("email") if isinstance(contact, dict) else None,
                student_id=data.get("studentId"),
            )
            return self._user

        # Fallback: parse from the web page
        soup = await self._web_get("/webapps/portal/frameset.jsp")
        if soup:
            name_elem = soup.select_one(
                "#global-nav-user-display-name, .user-display-name, #topframe"
            )
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
        """List courses the student is enrolled in."""
        bb_courses: list[Course] = []
        memberships = await self._api_get("/users/me/courses", params={"limit": 100})
        if not memberships or "results" not in memberships:
            profile = await self.get_user_profile()
            if profile and profile.id and profile.id != "unknown":
                memberships = await self._api_get(
                    f"/users/{profile.id}/courses", params={"limit": 100}
                )

        if memberships and "results" in memberships:
            course_ids = [
                m["courseId"]
                for m in memberships["results"]
                if m.get("courseRoleId") in ("Student", "student", "S", "OriginalStudent", "UltraStudent", None)
            ]
            if not course_ids:
                course_ids = [m["courseId"] for m in memberships["results"]]

            async def fetch_one(cid: str) -> Course | None:
                data = await self._api_get(f"/courses/{cid}")
                if not data or not isinstance(data, dict):
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

            results = await asyncio.gather(
                *[fetch_one(cid) for cid in course_ids],
                return_exceptions=True,
            )
            bb_courses = [c for c in results if isinstance(c, Course)]

        if not bb_courses:
            bb_courses = await self._scrape_courses()

        return bb_courses

    async def _scrape_courses(self) -> list[Course]:
        """Scrape enrolled courses from the Blackboard web interface."""
        soup = await self._web_get("/webapps/portal/frameset.jsp")
        if not soup:
            return []

        courses = []
        for link in soup.select('a[href*="/webapps/blackboard/execute/launcher?type=Course"]'):
            href = link.get("href", "")
            name = link.get_text(strip=True)
            match = re.search(r"id=(_\d+_\d+)", href)
            if not match:
                continue  # skip links where we can't extract a clean ID
            course_id = match.group(1)
            if name and course_id:
                courses.append(Course(
                    id=course_id,
                    course_id=course_id,
                    name=name,
                    url=urljoin(settings.base_url, href),
                ))
        return courses

    # ── Announcements ────────────────────────────

    async def get_announcements(
        self, course_id: str, course_name: str, limit: int = 10
    ) -> list[Announcement]:
        """Fetch announcements for a specific course."""
        data = await self._api_get(
            f"/courses/{course_id}/announcements",
            params={"limit": limit, "sort": "created", "order": "desc"},
        )

        if data and "results" in data:
            announcements = []
            for item in data["results"]:
                creator_raw = item.get("creator")
                creator = (
                    creator_raw.get("name", {}).get("given", "")
                    if isinstance(creator_raw, dict)
                    else None
                )
                announcements.append(Announcement(
                    id=item.get("id", ""),
                    course_id=course_id,
                    course_name=course_name,
                    title=item.get("title", "Untitled"),
                    body=_html_to_text(item.get("body", "")),
                    creator=creator,
                    created=_parse_bb_datetime(item.get("created")),
                    modified=_parse_bb_datetime(item.get("modified")),
                ))
            return announcements

        return await self._scrape_announcements(course_id, course_name)

    async def _scrape_announcements(
        self, course_id: str, course_name: str
    ) -> list[Announcement]:
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
                grading = item.get("grading")
                due_date = _parse_bb_datetime(
                    grading.get("due") if isinstance(grading, dict) else None
                )
                handler = item.get("contentHandler")
                url = (
                    urljoin(settings.base_url, handler.get("url", ""))
                    if isinstance(handler, dict) and handler.get("url")
                    else None
                )
                assignments.append(Assignment(
                    id=item.get("id", ""),
                    course_id=course_id,
                    course_name=course_name,
                    title=item.get("title") or "Untitled",
                    due_date=due_date,
                    description=_html_to_text(item.get("body", "")),
                    url=url,
                ))

        if not assignments:
            assignments = await self._assignments_from_gradebook(course_id, course_name)

        cal_assignments = await self._assignments_from_calendar(course_id, course_name)
        existing_titles = {a.title.lower() for a in assignments}
        for ca in cal_assignments:
            if ca.title.lower() not in existing_titles:
                assignments.append(ca)

        return assignments

    async def _assignments_from_calendar(
        self, course_id: str, course_name: str
    ) -> list[Assignment]:
        """Fetch items from Blackboard calendar for a specific course."""
        data = await self._api_get("/calendars/items", params={"limit": 100})
        if not data or "results" not in data:
            return []

        assignments = []
        seen = set()
        for item in data["results"]:
            if item.get("calendarId") == course_id or course_id in str(item.get("calendarName", "")):
                due_date = _parse_bb_datetime(item.get("start") or item.get("end"))
                title = item.get("title") or "Untitled"
                key = (title, due_date)
                if key not in seen:
                    seen.add(key)
                    assignments.append(Assignment(
                        id=item.get("id", ""),
                        course_id=course_id,
                        course_name=course_name,
                        title=title,
                        due_date=due_date,
                    ))
        return assignments

    async def _assignments_from_gradebook(
        self, course_id: str, course_name: str
    ) -> list[Assignment]:
        """Derive assignments from gradebook columns (which carry due dates)."""
        data = await self._api_get(
            f"/courses/{course_id}/gradebook/columns",
            params={"limit": 100},
        )
        if not data or "results" not in data:
            return []

        return [
            Assignment(
                id=col.get("id", ""),
                course_id=course_id,
                course_name=course_name,
                title=col.get("name") or "Untitled",
                due_date=_parse_bb_datetime(col.get("due")),
                max_score=col.get("points"),
            )
            for col in data["results"]
            if col.get("contentId")
        ]

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
                col_id = item.get("columnId", "")
                col_name = item.get("columnName") or col_id or "Unknown"
                grades.append(GradeEntry(
                    column_name=col_name,
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
            if not name_elem:
                continue
            name = name_elem.get_text(strip=True)
            if not name:
                continue
            score_text = score_elem.get_text(strip=True) if score_elem else ""
            score, max_score = None, None
            m = re.match(r"([\d.]+)\s*/\s*([\d.]+)", score_text)
            if m:
                score = float(m.group(1))
                max_score = float(m.group(2))
            elif re.match(r"[\d.]+", score_text):
                score = float(re.match(r"[\d.]+", score_text).group())  # type: ignore[union-attr]
            grades.append(GradeEntry(
                column_name=name,
                score=score,
                max_score=max_score,
                status="graded" if score is not None else "pending",
            ))
        return grades

    # ── Course content ────────────────────────────

    async def get_course_content(
        self, course_id: str, folder_id: str | None = None
    ) -> list[ContentItem]:
        """Fetch course content (folders, files, links, assignments)."""
        path = (
            f"/courses/{course_id}/contents/{folder_id}/children"
            if folder_id
            else f"/courses/{course_id}/contents"
        )
        data = await self._api_get(path, params={"limit": 100})

        if data and "results" in data:
            items = []
            for item in data["results"]:
                handler = item.get("contentHandler", {})
                handler_id = handler.get("id", "") if isinstance(handler, dict) else ""
                content_type = _map_handler_to_type(handler_id)
                url = (
                    urljoin(settings.base_url, handler.get("url", ""))
                    if isinstance(handler, dict) and handler.get("url")
                    else None
                )
                items.append(ContentItem(
                    id=item.get("id", ""),
                    title=item.get("title") or "Untitled",
                    content_type=content_type,
                    description=_html_to_text(item.get("body", "")),
                    url=url,
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
