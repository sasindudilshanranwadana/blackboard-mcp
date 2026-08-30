"""
blackboard/models.py — Pydantic data models for Blackboard entities.

These are the core data structures passed between the client and tools.
All datetimes are stored as UTC-aware datetime objects.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# ──────────────────────────────────────────────
#  Core entities
# ──────────────────────────────────────────────

class UserProfile(BaseModel):
    """The currently authenticated student."""
    id: str
    username: str
    given_name: str
    family_name: str
    email: str | None = None
    student_id: str | None = None

    @property
    def full_name(self) -> str:
        return f"{self.given_name} {self.family_name}".strip()


class Course(BaseModel):
    """A Blackboard course / unit the student is enrolled in."""
    id: str                                   # Internal Blackboard ID e.g. "_12345_1"
    course_id: str                            # Human-readable code e.g. "COMP101_2024_S1"
    name: str
    instructor: str | None = None
    term: str | None = None
    is_available: bool = True
    description: str | None = None
    url: str | None = None                 # Direct link to the course in Blackboard


class Announcement(BaseModel):
    """An announcement posted in a course."""
    id: str
    course_id: str
    course_name: str
    title: str
    body: str
    creator: str | None = None
    created: datetime | None = None
    modified: datetime | None = None


class Assignment(BaseModel):
    """An assignment or assessment item in a course."""
    id: str
    course_id: str
    course_name: str
    title: str
    due_date: datetime | None = None
    max_score: float | None = None
    score: float | None = None
    status: str | None = None             # e.g. "submitted", "graded", "needs_submission"
    description: str | None = None
    url: str | None = None


class GradeEntry(BaseModel):
    """A single row in a course gradebook."""
    column_name: str
    score: float | None = None
    max_score: float | None = None
    status: str | None = None
    feedback: str | None = None

    @property
    def percentage(self) -> float | None:
        if self.score is not None and self.max_score and self.max_score > 0:
            return round((self.score / self.max_score) * 100, 1)
        return None


class ContentItem(BaseModel):
    """A content item inside a course (folder, file, link, etc.)."""
    id: str
    title: str
    content_type: str                        # e.g. "document", "folder", "assignment", "link"
    description: str | None = None
    url: str | None = None
    children: list[ContentItem] = Field(default_factory=list)
