"""
server.py — Blackboard MCP Server

MCP server that connects any AI assistant to a student's university
Blackboard LMS account. Works with Blackboard Ultra and Classic at
any university worldwide.

Supported platforms: Claude Desktop, Claude Code, Cursor, Windsurf,
                     Cline, Zed, Continue, Codex CLI, Gemini CLI.

Run directly with:
    python server.py

Or configure in your AI assistant's MCP config. See README.md.
"""
from __future__ import annotations

import asyncio
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

from blackboard.auth import (
    LoginTimeoutError,
    NotConfiguredError,
    clear_cookie_cache,
    interactive_login,
)
from blackboard.client import BlackboardClient

# ──────────────────────────────────────────────
#  Version & auto-update
# ──────────────────────────────────────────────

PROJECT_DIR = Path(__file__).parent
REPO = "sasindudilshanranwadana/blackboard-mcp"
_VERSION = (PROJECT_DIR / "VERSION").read_text().strip() if (PROJECT_DIR / "VERSION").exists() else "unknown"
_update_notice: str | None = None


async def _check_for_updates() -> None:
    global _update_notice
    try:
        async with httpx.AsyncClient(timeout=6) as http:
            r = await http.get(
                f"https://api.github.com/repos/{REPO}/contents/VERSION",
                headers={"Accept": "application/vnd.github.raw"},
            )
            if r.status_code == 200:
                latest = r.text.strip()
                if latest != _VERSION:
                    _update_notice = (
                        f"\n\n---\n"
                        f"💡 **Update available:** you have `v{_VERSION}`, latest is `v{latest}`.  "
                        f"Ask me: _\"Update Blackboard MCP\"_ to install it automatically."
                    )
    except Exception:
        pass


# ──────────────────────────────────────────────
#  Server initialisation
# ──────────────────────────────────────────────

mcp = FastMCP(
    name="Blackboard MCP",
    instructions=(
        "You are a helpful assistant connected to the student's university Blackboard LMS. "
        "You can look up courses, announcements, assignments, grades, and course content. "
        "Always present information in a clear, organised way. "
        "When showing due dates, highlight anything due within 3 days. "
        "When grades are available, calculate percentages and note if something is still pending. "
        "If the student has not connected their Blackboard account yet, use the connect_blackboard "
        "tool to guide them through setup — ask for their university's Blackboard URL first."
    ),
)

_client: BlackboardClient | None = None
_client_lock = asyncio.Lock()


async def get_client() -> BlackboardClient:
    """Return the shared BlackboardClient, initialising it on first call."""
    from blackboard.auth import NotConfiguredError as _NCE
    from config import settings as _s
    if not _s.is_configured():
        raise _NCE("No Blackboard URL is configured.")
    global _client
    async with _client_lock:
        if _client is None:
            _client = BlackboardClient()
            await _client.initialize()
    return _client


def _reset_client() -> None:
    """Force the client to be re-created on the next call (used after reconnect)."""
    global _client
    _client = None


# ──────────────────────────────────────────────
#  Not-configured guard
# ──────────────────────────────────────────────

_NOT_CONFIGURED = (
    "## 🔗 Connect your Blackboard account first\n\n"
    "It looks like your university's Blackboard hasn't been connected yet.\n\n"
    "**To get started, just tell me:**\n"
    "> *\"Connect my Blackboard — my university URL is https://blackboard.myuniversity.edu\"*\n\n"
    "Or run the setup wizard in your terminal:\n"
    "```\npython3 setup.py\n```\n\n"
    "Once connected, all tools will work automatically."
)


# ──────────────────────────────────────────────
#  Formatting helpers
# ──────────────────────────────────────────────

def _fmt_dt(dt: datetime | None, show_relative: bool = False) -> str:
    if dt is None:
        return "No date"
    s = dt.strftime("%a %d %b %Y, %I:%M %p %Z")
    if show_relative:
        now = datetime.now(timezone.utc)
        diff = dt - now
        days = diff.days
        if days < 0:
            s += "  _(overdue)_"
        elif days == 0:
            s += "  🔴 **DUE TODAY**"
        elif days == 1:
            s += "  🔴 **DUE TOMORROW**"
        elif days <= 3:
            s += f"  🟠 _(in {days} days)_"
        elif days <= 7:
            s += f"  🟡 _(in {days} days)_"
        else:
            s += f"  🟢 _(in {days} days)_"
    return s


def _urgency_emoji(dt: datetime | None) -> str:
    if dt is None:
        return "⚪"
    now = datetime.now(timezone.utc)
    diff = (dt - now).days
    if diff < 0:
        return "⚫"
    if diff <= 1:
        return "🔴"
    if diff <= 3:
        return "🟠"
    if diff <= 7:
        return "🟡"
    return "🟢"


# ──────────────────────────────────────────────
#  MCP Tools
# ──────────────────────────────────────────────

@mcp.tool()
async def connect_blackboard(university_blackboard_url: str) -> str:
    """
    Connect to your university's Blackboard LMS. Call this first before using
    any other tool. Opens a browser window so you can log in with your normal
    university credentials (supports any SSO, Microsoft, Shibboleth, MFA, etc).

    Args:
        university_blackboard_url: Your university's Blackboard URL.
            Examples: https://blackboard.myuniversity.edu
                      https://learn.myuni.edu.au
                      https://lms.myschool.ac.uk
    """
    # Normalise URL
    url = university_blackboard_url.strip().rstrip("/")
    if not url.startswith("http"):
        url = "https://" + url

    # Strict URL validation — anchored, no whitespace/newlines, only safe URL chars.
    # Prevents .env line injection via crafted input like "https://x.edu\nBB_FOO=bar".
    if not re.fullmatch(r"https?://[a-zA-Z0-9.\-]+(:\d+)?(/[a-zA-Z0-9._\-/]*)?", url):
        return (
            "❌ That doesn't look like a valid URL.\n\n"
            "Please provide your university's full Blackboard address, for example:\n"
            "> `https://blackboard.myuniversity.edu`"
        )

    # Detect interface (Ultra vs Classic)
    interface = "ultra"
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as http:
            resp = await http.get(f"{url}/ultra/institution-page")
            final = str(resp.url)
            host = url.split("//")[-1].split("/")[0]
            if "/ultra/" in final and host in final:
                interface = "ultra"
            else:
                interface = "classic"
    except Exception:
        pass

    # Write .env
    env_file = PROJECT_DIR / ".env"
    env_data: dict[str, str] = {}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env_data[k.strip()] = v.strip()
    env_data["BB_BASE_URL"] = url
    env_data["BB_INTERFACE"] = interface
    env_file.write_text("\n".join(f"{k}={v}" for k, v in env_data.items()) + "\n")

    # Update the live settings object directly — no module reload needed
    from config import settings as _settings
    _settings.base_url = url
    _settings.interface = interface

    # Reset the cached client so it is rebuilt with the new URL
    _reset_client()

    # Clear any stale cookies from a previous university
    clear_cookie_cache()

    iface_label = "Ultra (modern)" if interface == "ultra" else "Classic (legacy)"

    try:
        cookies = await interactive_login(base_url=url)
    except LoginTimeoutError:
        return (
            "⏰ **Login cancelled** — no cookie was entered.\n\n"
            f"Please try again: *\"Connect my Blackboard — {url}\"*"
        )
    except Exception as exc:
        return (
            f"❌ **Login failed:** {exc}\n\n"
            "Please try again."
        )

    if not cookies:
        return (
            "⚠️ Login appeared to complete but no session cookies were captured.\n\n"
            "Please try again or run the setup wizard:\n"
            "```\npython3 setup.py\n```"
        )

    return (
        f"## ✅ Blackboard Connected!\n\n"
        f"**University URL:** `{url}`\n"
        f"**Interface:** {iface_label}\n"
        f"**Session:** Active ({len(cookies)} cookies captured)\n\n"
        f"You're all set! Try asking:\n"
        f"- *\"What courses am I enrolled in?\"*\n"
        f"- *\"What assignments are due this week?\"*\n"
        f"- *\"Catch me up on everything in Blackboard\"*"
    )


@mcp.tool()
async def get_my_profile() -> str:
    """
    Return the logged-in student's profile: name, student ID, and email.
    Use this to confirm whose account is connected.
    """
    try:
        client = await get_client()
    except NotConfiguredError:
        return _NOT_CONFIGURED

    profile = await client.get_user_profile()
    if not profile:
        return "❌ Could not retrieve your profile. Try reconnecting: *\"Connect my Blackboard\"*"

    lines = [
        "## 👤 Your Student Profile",
        "",
        f"**Name:** {profile.full_name}",
        f"**Username / Student Number:** {profile.username}",
    ]
    if profile.student_id:
        lines.append(f"**Student ID:** {profile.student_id}")
    if profile.email:
        lines.append(f"**Email:** {profile.email}")

    return "\n".join(lines)


@mcp.tool()
async def list_courses() -> str:
    """
    List all courses the student is currently enrolled in on Blackboard.
    Shows course name, course code, and term.
    """
    try:
        client = await get_client()
    except NotConfiguredError:
        return _NOT_CONFIGURED

    courses = await client.get_courses()
    if not courses:
        return (
            "No active courses found on your Blackboard account.\n"
            "This may mean you have no current enrolments, or the session needs refreshing.\n\n"
            "Try: *\"Reconnect my Blackboard\"*"
        )

    lines = [f"## 📚 Your Enrolled Courses ({len(courses)} total)", ""]
    for i, course in enumerate(courses, 1):
        avail = "✅" if course.is_available else "🔒"
        term = f" · {course.term}" if course.term else ""
        lines.append(f"### {i}. {avail} {course.name}")
        lines.append(f"**Code:** `{course.course_id}`{term}")
        if course.instructor:
            lines.append(f"**Instructor:** {course.instructor}")
        if course.description:
            desc = course.description[:200] + "…" if len(course.description) > 200 else course.description
            lines.append(f"_{desc}_")
        if course.url:
            lines.append(f"[Open in Blackboard]({course.url})")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
async def get_course_details(course_name_or_code: str) -> str:
    """
    Get detailed information about a specific course by name or code.

    Args:
        course_name_or_code: Part of the course name or code to search for (case-insensitive).
    """
    try:
        client = await get_client()
    except NotConfiguredError:
        return _NOT_CONFIGURED

    courses = await client.get_courses()
    query = course_name_or_code.lower()
    matches = [c for c in courses if query in c.name.lower() or query in c.course_id.lower()]

    if not matches:
        all_names = "\n".join(f"- {c.name} (`{c.course_id}`)" for c in courses)
        return (
            f"No course found matching **'{course_name_or_code}'**.\n\n"
            f"Your enrolled courses are:\n{all_names}"
        )

    course = matches[0]
    lines = [
        f"## 📖 {course.name}", "",
        f"**Course Code:** `{course.course_id}`",
        f"**Blackboard ID:** `{course.id}`",
        f"**Status:** {'Available ✅' if course.is_available else 'Not available 🔒'}",
    ]
    if course.term:
        lines.append(f"**Term:** {course.term}")
    if course.instructor:
        lines.append(f"**Instructor:** {course.instructor}")
    if course.description:
        lines.append(f"\n**Description:**\n{course.description}")
    if course.url:
        lines.append(f"\n[Open in Blackboard]({course.url})")

    return "\n".join(lines)


@mcp.tool()
async def get_announcements(course_name_or_code: str | None = None, limit: int = 5) -> str:
    """
    Fetch recent announcements from your courses.

    Args:
        course_name_or_code: Filter to a specific course (optional). If omitted, fetches from all courses.
        limit: Max announcements per course (default 5).
    """
    try:
        client = await get_client()
    except NotConfiguredError:
        return _NOT_CONFIGURED

    courses = await client.get_courses()

    if course_name_or_code:
        query = course_name_or_code.lower()
        courses = [c for c in courses if query in c.name.lower() or query in c.course_id.lower()]
        if not courses:
            return f"No course found matching **'{course_name_or_code}'**."

    all_announcements = []
    for course in courses:
        anns = await client.get_announcements(course.id, course.name, limit=limit)
        all_announcements.extend(anns)

    if not all_announcements:
        scope = f"**{courses[0].name}**" if len(courses) == 1 else "any of your courses"
        return f"📭 No announcements found in {scope}."

    all_announcements.sort(key=lambda a: a.created or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    lines = [f"## 📢 Announcements ({len(all_announcements)} found)", ""]
    for ann in all_announcements:
        lines.append(f"### {ann.title}")
        lines.append(f"**Course:** {ann.course_name}")
        if ann.created:
            lines.append(f"**Posted:** {_fmt_dt(ann.created)}")
        if ann.creator:
            lines.append(f"**By:** {ann.creator}")
        if ann.body:
            body = ann.body[:500] + "…" if len(ann.body) > 500 else ann.body
            lines.append(f"\n{body}")
        lines.append("\n---")

    return "\n".join(lines)


@mcp.tool()
async def get_assignments(course_name_or_code: str | None = None) -> str:
    """
    List all assignments and assessments across your courses.

    Args:
        course_name_or_code: Filter to a specific course (optional).
    """
    try:
        client = await get_client()
    except NotConfiguredError:
        return _NOT_CONFIGURED

    courses = await client.get_courses()

    if course_name_or_code:
        query = course_name_or_code.lower()
        courses = [c for c in courses if query in c.name.lower() or query in c.course_id.lower()]
        if not courses:
            return f"No course found matching **'{course_name_or_code}'**."

    all_assignments = []
    for course in courses:
        assignments = await client.get_assignments(course.id, course.name)
        all_assignments.extend(assignments)

    if not all_assignments:
        return "📭 No assignments found."

    def sort_key(a):
        return a.due_date if a.due_date else datetime.max.replace(tzinfo=timezone.utc)

    all_assignments.sort(key=sort_key)

    lines = [f"## 📝 Assignments ({len(all_assignments)} found)", ""]
    for a in all_assignments:
        emoji = _urgency_emoji(a.due_date)
        lines.append(f"### {emoji} {a.title}")
        lines.append(f"**Course:** {a.course_name}")
        lines.append(f"**Due:** {_fmt_dt(a.due_date, show_relative=True)}")
        if a.max_score is not None:
            lines.append(f"**Worth:** {a.max_score} marks")
        if a.status:
            lines.append(f"**Status:** {a.status.replace('_', ' ').title()}")
        if a.description:
            desc = a.description[:300] + "…" if len(a.description) > 300 else a.description
            lines.append(f"\n_{desc}_")
        if a.url:
            lines.append(f"[Open Assignment]({a.url})")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
async def get_due_dates(days_ahead: int = 14) -> str:
    """
    Show upcoming assignment due dates across all your courses, sorted by urgency.

    Args:
        days_ahead: How many days ahead to look (default 14 = two weeks).
    """
    try:
        client = await get_client()
    except NotConfiguredError:
        return _NOT_CONFIGURED

    courses = await client.get_courses()
    cutoff = datetime.now(timezone.utc) + timedelta(days=days_ahead)

    upcoming = []
    for course in courses:
        for a in await client.get_assignments(course.id, course.name):
            # Include overdue, upcoming within window, and undated items
            if a.due_date is None or a.due_date <= cutoff:
                upcoming.append(a)

    if not upcoming:
        return f"🎉 Nothing due in the next {days_ahead} days! Enjoy the break."

    upcoming.sort(key=lambda a: a.due_date if a.due_date else datetime.max.replace(tzinfo=timezone.utc))

    lines = [
        f"## ⏰ Upcoming Due Dates — Next {days_ahead} Days", "",
        "| Urgency | Assignment | Course | Due Date |",
        "|---------|-----------|--------|----------|",
    ]
    for a in upcoming:
        emoji = _urgency_emoji(a.due_date)
        due_str = _fmt_dt(a.due_date) if a.due_date else "_No date set_"
        title = a.title[:45] + "…" if len(a.title) > 45 else a.title
        course = a.course_name[:30] + "…" if len(a.course_name) > 30 else a.course_name
        lines.append(f"| {emoji} | {title} | {course} | {due_str} |")

    lines += [
        "",
        "**Key:** 🔴 Due within 24h · 🟠 Within 3 days · 🟡 Within 1 week · 🟢 More than 1 week · ⚫ Overdue",
    ]
    return "\n".join(lines)


@mcp.tool()
async def get_grades(course_name_or_code: str) -> str:
    """
    Show your grades / gradebook for a specific course.

    Args:
        course_name_or_code: Part of the course name or code to search for.
    """
    try:
        client = await get_client()
    except NotConfiguredError:
        return _NOT_CONFIGURED

    courses = await client.get_courses()
    query = course_name_or_code.lower()
    matches = [c for c in courses if query in c.name.lower() or query in c.course_id.lower()]

    if not matches:
        all_names = ", ".join(f"`{c.course_id}`" for c in courses)
        return f"No course found matching **'{course_name_or_code}'**.\nYour courses: {all_names}"

    course = matches[0]
    grades = await client.get_grades(course.id)

    if not grades:
        return (
            f"📭 No grade entries found for **{course.name}**.\n"
            "Grades may not have been released yet."
        )

    lines = [f"## 📊 Grades — {course.name}", ""]
    graded = [g for g in grades if g.score is not None]
    pending = [g for g in grades if g.score is None]

    if graded:
        lines.append("### ✅ Released Grades")
        lines.append("")
        lines.append("| Assessment | Score | Max | % | Status |")
        lines.append("|-----------|-------|-----|---|--------|")
        for g in graded:
            pct = f"{g.percentage}%" if g.percentage is not None else "—"
            max_s = str(g.max_score) if g.max_score is not None else "—"
            score_s = str(g.score) if g.score is not None else "—"
            status = g.status or "—"
            if g.percentage is not None:
                if g.percentage >= 85:
                    pct = f"🌟 {pct}"
                elif g.percentage >= 65:
                    pct = f"✅ {pct}"
                elif g.percentage >= 50:
                    pct = f"⚠️ {pct}"
                else:
                    pct = f"❌ {pct}"
            lines.append(f"| {g.column_name} | {score_s} | {max_s} | {pct} | {status} |")

        scores = [(g.score, g.max_score) for g in graded if g.score is not None and g.max_score]
        if scores:
            total_score = sum(s for s, _ in scores)
            total_max = sum(m for _, m in scores)
            overall_pct = round((total_score / total_max) * 100, 1) if total_max > 0 else None
            lines.append("")
            lines.append(f"**Overall (graded items):** {total_score} / {total_max} = **{overall_pct}%**")

    if pending:
        lines.append("")
        lines.append("### ⏳ Pending / Not Yet Released")
        for g in pending:
            lines.append(f"- {g.column_name}" + (f" _(status: {g.status})_" if g.status else ""))

    return "\n".join(lines)


@mcp.tool()
async def get_course_content(course_name_or_code: str, folder: str | None = None) -> str:
    """
    Browse content (files, folders, links, assignments) inside a course.

    Args:
        course_name_or_code: Part of the course name or code to search for.
        folder: Optional folder name to look inside (e.g. "Week 3"). If omitted, shows top-level content.
    """
    try:
        client = await get_client()
    except NotConfiguredError:
        return _NOT_CONFIGURED

    courses = await client.get_courses()
    query = course_name_or_code.lower()
    matches = [c for c in courses if query in c.name.lower() or query in c.course_id.lower()]

    if not matches:
        return f"No course found matching **'{course_name_or_code}'**."

    course = matches[0]
    items = await client.get_course_content(course.id)

    if folder:
        folder_query = folder.lower()
        folder_item = next(
            (i for i in items if folder_query in i.title.lower() and i.content_type == "folder"),
            None,
        )
        if folder_item:
            items = await client.get_course_content(course.id, folder_item.id)
        else:
            folders = [i for i in items if i.content_type == "folder"]
            folder_list = "\n".join(f"- 📁 {f.title}" for f in folders) or "_No folders found_"
            return (
                f"Folder **'{folder}'** not found in **{course.name}**.\n\n"
                f"Available top-level folders:\n{folder_list}"
            )

    if not items:
        return f"No content found in **{course.name}**."

    type_icons = {
        "folder": "📁", "document": "📄", "file": "📎",
        "assignment": "📝", "link": "🔗", "video": "🎬",
        "discussion": "💬", "page": "📃", "item": "•",
    }

    level = f" > {folder}" if folder else ""
    lines = [f"## 📂 {course.name}{level}", f"_{len(items)} items_", ""]

    for item in items:
        icon = type_icons.get(item.content_type, "•")
        lines.append(f"### {icon} {item.title}")
        lines.append(f"**Type:** {item.content_type.title()}")
        if item.description:
            desc = item.description[:200] + "…" if len(item.description) > 200 else item.description
            lines.append(f"_{desc}_")
        if item.url:
            lines.append(f"[Open]({item.url})")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
async def summarize_activity() -> str:
    """
    Give a comprehensive digest of everything happening across all your courses:
    recent announcements, upcoming deadlines, and pending assignments.
    Perfect for a quick 'catch me up' overview.
    """
    try:
        client = await get_client()
    except NotConfiguredError:
        return _NOT_CONFIGURED

    profile_task = asyncio.create_task(client.get_user_profile())
    courses_task = asyncio.create_task(client.get_courses())

    profile = await profile_task
    courses = await courses_task

    if not courses:
        return (
            "No courses found on your Blackboard account.\n\n"
            "If you've just connected, it may take a moment. Try again shortly.\n"
            "If this keeps happening, try reconnecting: *\"Connect my Blackboard\"*"
        )

    ann_tasks = [client.get_announcements(c.id, c.name, limit=2) for c in courses]
    asgn_tasks = [client.get_assignments(c.id, c.name) for c in courses]

    ann_results = await asyncio.gather(*ann_tasks, return_exceptions=True)
    asgn_results = await asyncio.gather(*asgn_tasks, return_exceptions=True)

    all_announcements = [a for r in ann_results if isinstance(r, list) for a in r]
    all_assignments = [a for r in asgn_results if isinstance(r, list) for a in r]

    now = datetime.now(timezone.utc)
    cutoff_7 = now + timedelta(days=7)
    cutoff_14 = now + timedelta(days=14)

    urgent = sorted(
        [a for a in all_assignments if a.due_date and a.due_date <= cutoff_7],
        key=lambda a: a.due_date,
    )
    upcoming = [a for a in all_assignments if a.due_date and cutoff_7 < a.due_date <= cutoff_14]
    all_announcements.sort(
        key=lambda a: a.created or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    name = profile.given_name if profile else "Student"
    lines = [
        f"# 📋 Blackboard Summary for {name}",
        f"_{now.strftime('%A, %d %B %Y')}_",
        "",
        f"**Enrolled Courses:** {len(courses)}",
        "",
    ]

    if urgent:
        lines.append("## 🚨 Due This Week")
        lines.append("")
        for a in urgent:
            emoji = _urgency_emoji(a.due_date)
            lines.append(f"- {emoji} **{a.title}** — _{a.course_name}_ — {_fmt_dt(a.due_date, show_relative=True)}")
        lines.append("")

    if upcoming:
        lines.append("## 📅 Coming Up (Next 2 Weeks)")
        lines.append("")
        for a in upcoming:
            lines.append(f"- 🟢 **{a.title}** — _{a.course_name}_ — {_fmt_dt(a.due_date)}")
        lines.append("")

    recent_anns = all_announcements[:5]
    if recent_anns:
        lines.append("## 📢 Recent Announcements")
        lines.append("")
        for ann in recent_anns:
            date_str = _fmt_dt(ann.created) if ann.created else ""
            lines.append(f"### {ann.title}")
            lines.append(f"_{ann.course_name}_ · {date_str}")
            if ann.body:
                snippet = ann.body[:200] + "…" if len(ann.body) > 200 else ann.body
                lines.append(snippet)
            lines.append("")

    if not urgent and not upcoming:
        lines.append("## ✨ No Urgent Deadlines")
        lines.append("_You're all caught up! No assignments due in the next 2 weeks._")
        lines.append("")

    lines += [
        "---",
        f"_Last updated: {now.strftime('%I:%M %p')} · Use `get_assignments`, `get_grades`, or `get_announcements` for more detail._",
    ]

    result = "\n".join(lines)
    if _update_notice:
        result += _update_notice
    return result


@mcp.tool()
async def search_announcements(query: str, course_name_or_code: str | None = None) -> str:
    """
    Search course announcements for specific keywords like 'exam', 'extension', 'cancelled', 'zoom', 'room'.

    Args:
        query: Word or phrase to search for (case-insensitive).
        course_name_or_code: Optional course name/code to limit the search. Searches all courses if omitted.
    """
    try:
        client = await get_client()
    except NotConfiguredError:
        return _NOT_CONFIGURED

    query_lower = query.lower().strip()
    courses = await client.get_courses()
    if course_name_or_code:
        course = client._match_course(courses, course_name_or_code)
        if not course:
            return f"❌ Course '{course_name_or_code}' not found."
        target_courses = [course]
    else:
        target_courses = courses

    matches = []
    for c in target_courses:
        anns = await client.get_announcements(c.id, c.name, limit=20)
        for ann in anns:
            if query_lower in ann.title.lower() or (ann.body and query_lower in ann.body.lower()):
                matches.append(ann)

    if not matches:
        scope = f" in **{course_name_or_code}**" if course_name_or_code else " across all courses"
        return f"🔍 No announcements matching **'{query}'** found{scope}."

    lines = [f"## 🔍 Announcements matching '{query}' ({len(matches)} found)", ""]
    for ann in matches:
        date_str = _fmt_dt(ann.created) if ann.created else "Recent"
        lines.append(f"### 📢 {ann.title}")
        lines.append(f"**Course:** {ann.course_name} · **Date:** {date_str}")
        if ann.body:
            body_preview = ann.body[:350] + "…" if len(ann.body) > 350 else ann.body
            lines.append(f"\n{body_preview}")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
async def calculate_gpa_and_standing() -> str:
    """
    Calculate current academic progress and standing across all enrolled courses.
    Shows completed assessments, earned points, weighted percentages, and grading bands (HD/D/C/P).
    """
    try:
        client = await get_client()
    except NotConfiguredError:
        return _NOT_CONFIGURED

    courses = await client.get_courses()
    if not courses:
        return "📭 No courses found."

    lines = ["## 🎓 Academic Progress & Standing Summary", ""]

    total_earned_all = 0.0
    total_possible_all = 0.0
    graded_courses_count = 0

    for course in courses:
        grades = await client.get_grades(course.id)
        graded = [g for g in grades if g.score is not None and g.max_score and g.max_score > 0]
        pending = [g for g in grades if g.score is None]

        if not graded and not pending:
            continue

        lines.append(f"### 📚 {course.name} ({course.course_id})")
        if graded:
            course_earned = sum(g.score for g in graded if g.score is not None)
            course_possible = sum(g.max_score for g in graded if g.max_score is not None)
            course_pct = round((course_earned / course_possible) * 100, 1) if course_possible > 0 else 0.0

            # Australian standard grading bands
            if course_pct >= 85:
                standing = "🌟 High Distinction (HD)"
            elif course_pct >= 75:
                standing = "✨ Distinction (D)"
            elif course_pct >= 65:
                standing = "✅ Credit (C)"
            elif course_pct >= 50:
                standing = "🟡 Pass (P)"
            else:
                standing = "❌ Fail / Needs Attention (N)"

            lines.append(f"- **Current Average:** `{course_pct}%` ({course_earned:.1f}/{course_possible:.1f} pts) — **{standing}**")
            lines.append(f"- **Completed Tasks:** {len(graded)} graded")
            total_earned_all += course_earned
            total_possible_all += course_possible
            graded_courses_count += 1
        else:
            lines.append("- **Current Average:** _No graded items released yet_")

        if pending:
            lines.append(f"- **Pending / Upcoming:** {len(pending)} assessments remaining")
        lines.append("")

    if graded_courses_count > 0 and total_possible_all > 0:
        overall_pct = round((total_earned_all / total_possible_all) * 100, 1)
        lines.append("---")
        lines.append(f"### 📈 Cumulative Standing: **{overall_pct}%** ({total_earned_all:.1f}/{total_possible_all:.1f} Total Points)")

    return "\n".join(lines)


@mcp.tool()
async def update_server() -> str:
    """
    Update the Blackboard MCP server to the latest version from GitHub.
    Pulls new code and reinstalls any updated dependencies automatically.
    """
    global _update_notice
    try:
        pull = subprocess.run(
            ["git", "-C", str(PROJECT_DIR), "pull", "--ff-only"],
            capture_output=True, text=True, timeout=30,
        )
        if pull.returncode != 0:
            return f"❌ git pull failed:\n```\n{pull.stderr.strip()}\n```"

        pull_out = pull.stdout.strip()
        already_latest = "Already up to date" in pull_out

        import sys
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "-r",
             str(PROJECT_DIR / "requirements.txt")],
            capture_output=True, text=True, timeout=120,
        )

        new_version = (PROJECT_DIR / "VERSION").read_text().strip() \
            if (PROJECT_DIR / "VERSION").exists() else "unknown"
        _update_notice = None

        if already_latest:
            return f"✅ **Already on the latest version** (`v{new_version}`)\nNo changes were needed."

        return "\n".join([
            f"## ✅ Blackboard MCP Updated to `v{new_version}`", "",
            "**Changes pulled:**",
            f"```\n{pull_out}\n```", "",
            "⚠️ **Restart your AI assistant** to load the new version.",
        ])

    except subprocess.TimeoutExpired:
        return "❌ Update timed out. Please run `git pull` manually in the install directory."
    except Exception as e:
        return f"❌ Update failed: {e}"


# ──────────────────────────────────────────────
#  Entry point
# ──────────────────────────────────────────────

if __name__ == "__main__":
    # Fire update check once at startup in the background — never blocks tool calls
    async def _startup() -> None:
        asyncio.create_task(_check_for_updates())

    asyncio.run(_startup())
    mcp.run(transport="stdio")
