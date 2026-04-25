"""
server.py — CDU Learnline MCP Server

Entry point for the Blackboard MCP server. Registers all student-facing
tools with FastMCP and connects them to the BlackboardClient.

Run directly with:
    python server.py

Or configure in Claude Desktop's config.json:
    {
      "mcpServers": {
        "blackboard-cdu": {
          "command": "python",
          "args": ["/path/to/Blackboard MCP/server.py"]
        }
      }
    }
"""
from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Any

from mcp.server.fastmcp import FastMCP

from blackboard.client import BlackboardClient
from blackboard.models import Course

# ──────────────────────────────────────────────
#  Server initialisation
# ──────────────────────────────────────────────

mcp = FastMCP(
    name="CDU Learnline",
    instructions=(
        "You are a helpful assistant with access to the student's Charles Darwin University "
        "Blackboard (Learnline) account. You can look up courses, announcements, assignments, "
        "grades, and course content. Always present information in a clear, organised way. "
        "When showing due dates, highlight anything due within 3 days. "
        "When grades are available, calculate percentages and note if something is still pending."
    ),
)

# Lazy-initialised singleton client — created on first tool call
_client: BlackboardClient | None = None
_client_lock = asyncio.Lock()


async def get_client() -> BlackboardClient:
    """Return the shared BlackboardClient, initialising it on first call."""
    global _client
    async with _client_lock:
        if _client is None:
            _client = BlackboardClient()
            await _client.initialize()
    return _client


# ──────────────────────────────────────────────
#  Formatting helpers
# ──────────────────────────────────────────────

def _fmt_dt(dt: datetime | None, show_relative: bool = False) -> str:
    """Format a datetime for display, optionally with a relative label."""
    if dt is None:
        return "No date"
    # Convert to local-ish display (keep UTC label for clarity)
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
        return "⚫"  # overdue
    if diff == 0:
        return "🔴"
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
async def get_my_profile() -> str:
    """
    Return the logged-in student's profile: name, student ID, and email.
    Use this to confirm whose account is connected.
    """
    client = await get_client()
    profile = await client.get_user_profile()

    if not profile:
        return "❌ Could not retrieve your profile. Check that you're logged in correctly."

    lines = [
        "## 👤 Your CDU Student Profile",
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
    List all courses (units) the student is currently enrolled in on Blackboard Learnline.
    Shows course name, course code, and term.
    """
    client = await get_client()
    courses = await client.get_courses()

    if not courses:
        return (
            "No active courses found on your Learnline account.\n"
            "This may mean you have no current enrolments, or the session needs refreshing."
        )

    lines = [
        f"## 📚 Your Enrolled Courses ({len(courses)} total)",
        "",
    ]

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
            lines.append(f"[Open in Learnline]({course.url})")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
async def get_course_details(course_name_or_code: str) -> str:
    """
    Get detailed information about a specific course by searching for it by name or code.

    Args:
        course_name_or_code: Part of the course name or code to search for (case-insensitive).
    """
    client = await get_client()
    courses = await client.get_courses()
    query = course_name_or_code.lower()

    matches = [
        c for c in courses
        if query in c.name.lower() or query in c.course_id.lower()
    ]

    if not matches:
        all_names = "\n".join(f"- {c.name} (`{c.course_id}`)" for c in courses)
        return (
            f"No course found matching **'{course_name_or_code}'**.\n\n"
            f"Your enrolled courses are:\n{all_names}"
        )

    course = matches[0]
    lines = [
        f"## 📖 {course.name}",
        "",
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
        lines.append(f"\n[Open in Learnline]({course.url})")

    return "\n".join(lines)


@mcp.tool()
async def get_announcements(course_name_or_code: str | None = None, limit: int = 5) -> str:
    """
    Fetch recent announcements from your courses.

    Args:
        course_name_or_code: Filter to a specific course (optional). If omitted, fetches from all courses.
        limit: Max announcements per course (default 5).
    """
    client = await get_client()
    courses = await client.get_courses()

    if course_name_or_code:
        query = course_name_or_code.lower()
        courses = [c for c in courses if query in c.name.lower() or query in c.course_id.lower()]
        if not courses:
            return f"No course found matching **'{course_name_or_code}'**."

    all_announcements = []
    for course in courses:
        announcements = await client.get_announcements(course.id, course.name, limit=limit)
        all_announcements.extend(announcements)

    if not all_announcements:
        scope = f"**{courses[0].name}**" if len(courses) == 1 else "any of your courses"
        return f"📭 No announcements found in {scope}."

    # Sort by date, newest first
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
    client = await get_client()
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

    # Sort by due date (no date goes to end)
    def sort_key(a):
        if a.due_date is None:
            return datetime.max.replace(tzinfo=timezone.utc)
        return a.due_date

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
    client = await get_client()
    courses = await client.get_courses()

    cutoff = datetime.now(timezone.utc) + timedelta(days=days_ahead)
    now = datetime.now(timezone.utc)

    upcoming = []
    for course in courses:
        assignments = await client.get_assignments(course.id, course.name)
        for a in assignments:
            if a.due_date and a.due_date >= now and a.due_date <= cutoff:
                upcoming.append(a)
            elif a.due_date is None:
                upcoming.append(a)  # include undated items too

    if not upcoming:
        return f"🎉 Nothing due in the next {days_ahead} days! Enjoy the break."

    upcoming.sort(key=lambda a: a.due_date if a.due_date else datetime.max.replace(tzinfo=timezone.utc))

    lines = [
        f"## ⏰ Upcoming Due Dates — Next {days_ahead} Days",
        "",
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
    client = await get_client()
    courses = await client.get_courses()
    query = course_name_or_code.lower()

    matches = [c for c in courses if query in c.name.lower() or query in c.course_id.lower()]
    if not matches:
        all_names = ", ".join(f"`{c.course_id}`" for c in courses)
        return f"No course found matching **'{course_name_or_code}'**.\nYour courses: {all_names}"

    course = matches[0]
    grades = await client.get_grades(course.id)

    if not grades:
        return f"📭 No grade entries found for **{course.name}**.\nThis may mean grades haven't been released yet."

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
            # Add emoji based on percentage
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

        # Overall summary
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
    client = await get_client()
    courses = await client.get_courses()
    query = course_name_or_code.lower()

    matches = [c for c in courses if query in c.name.lower() or query in c.course_id.lower()]
    if not matches:
        return f"No course found matching **'{course_name_or_code}'**."

    course = matches[0]
    items = await client.get_course_content(course.id)

    if folder:
        # Find the matching folder and get its children
        folder_query = folder.lower()
        folder_item = next(
            (i for i in items if folder_query in i.title.lower() and i.content_type == "folder"),
            None,
        )
        if folder_item:
            items = await client.get_course_content(course.id, folder_item.id)
        else:
            # Show available folders as a hint
            folders = [i for i in items if i.content_type == "folder"]
            folder_list = "\n".join(f"- 📁 {f.title}" for f in folders) or "_No folders found_"
            return (
                f"Folder **'{folder}'** not found in **{course.name}**.\n\n"
                f"Available top-level folders:\n{folder_list}"
            )

    if not items:
        return f"No content found in **{course.name}**."

    type_icons = {
        "folder": "📁",
        "document": "📄",
        "file": "📎",
        "assignment": "📝",
        "link": "🔗",
        "video": "🎬",
        "discussion": "💬",
        "page": "📃",
        "item": "•",
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
    client = await get_client()

    # Fetch everything concurrently
    profile_task = asyncio.create_task(client.get_user_profile())
    courses_task = asyncio.create_task(client.get_courses())

    profile = await profile_task
    courses = await courses_task

    if not courses:
        return "No courses found on your Learnline account."

    # Fetch announcements and assignments for all courses concurrently
    ann_tasks = [client.get_announcements(c.id, c.name, limit=2) for c in courses]
    asgn_tasks = [client.get_assignments(c.id, c.name) for c in courses]

    ann_results = await asyncio.gather(*ann_tasks)
    asgn_results = await asyncio.gather(*asgn_tasks)

    all_announcements = []
    for anns in ann_results:
        all_announcements.extend(anns)

    all_assignments = []
    for asgns in asgn_results:
        all_assignments.extend(asgns)

    now = datetime.now(timezone.utc)
    cutoff_7 = now + timedelta(days=7)
    cutoff_14 = now + timedelta(days=14)

    urgent = [a for a in all_assignments if a.due_date and a.due_date <= cutoff_7]
    upcoming = [a for a in all_assignments if a.due_date and cutoff_7 < a.due_date <= cutoff_14]
    no_date = [a for a in all_assignments if not a.due_date]

    urgent.sort(key=lambda a: a.due_date)
    all_announcements.sort(
        key=lambda a: a.created or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    name = profile.given_name if profile else "Student"
    lines = [
        f"# 📋 Learnline Summary for {name}",
        f"_{now.strftime('%A, %d %B %Y')}_",
        "",
        f"**Enrolled Courses:** {len(courses)}",
        "",
    ]

    # ── Urgent deadlines ──
    if urgent:
        lines.append("## 🚨 Due This Week")
        lines.append("")
        for a in urgent:
            emoji = _urgency_emoji(a.due_date)
            lines.append(f"- {emoji} **{a.title}** — _{a.course_name}_ — {_fmt_dt(a.due_date, show_relative=True)}")
        lines.append("")

    # ── Coming up ──
    if upcoming:
        lines.append("## 📅 Coming Up (Next 2 Weeks)")
        lines.append("")
        for a in upcoming:
            lines.append(f"- 🟢 **{a.title}** — _{a.course_name}_ — {_fmt_dt(a.due_date)}")
        lines.append("")

    # ── Recent announcements ──
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

    # ── Nothing urgent ──
    if not urgent and not upcoming:
        lines.append("## ✨ No Urgent Deadlines")
        lines.append("_You're all caught up! No assignments due in the next 2 weeks._")
        lines.append("")

    lines += [
        "---",
        f"_Last updated: {now.strftime('%I:%M %p')} · Use `get_assignments`, `get_grades`, or `get_announcements` for more detail._",
    ]

    return "\n".join(lines)


# ──────────────────────────────────────────────
#  Entry point
# ──────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
