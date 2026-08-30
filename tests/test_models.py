from datetime import datetime, timezone
from blackboard.models import Course, GradeEntry, Assignment, Announcement, UserProfile


def test_user_profile():
    profile = UserProfile(
        id="_123_1",
        username="sasi",
        given_name="Sasi",
        family_name="Ranwadana",
        email="sasi@example.edu.au",
        student_id="123456",
    )
    assert profile.full_name == "Sasi Ranwadana"


def test_course_model():
    course = Course(
        id="_12345_1",
        course_id="HLT1FPC-2026",
        name="Foundations of Professional Communication",
        term="2026 Semester 2",
        is_available=True,
    )
    assert course.course_id == "HLT1FPC-2026"
    assert course.name == "Foundations of Professional Communication"


def test_grade_entry_percentage():
    grade = GradeEntry(
        column_name="Assessment 1: Essay",
        score=85.0,
        max_score=100.0,
        status="Graded",
    )
    assert grade.score == 85.0
    assert grade.max_score == 100.0
    assert grade.percentage == 85.0


def test_grade_entry_percentage_none_when_no_score():
    grade = GradeEntry(
        column_name="Final Exam",
        score=None,
        max_score=100.0,
        status="Pending",
    )
    assert grade.percentage is None


def test_assignment_model():
    due = datetime(2026, 9, 15, 23, 59, tzinfo=timezone.utc)
    assignment = Assignment(
        id="_999_1",
        course_id="_12345_1",
        course_name="HLT1FPC",
        title="Final Research Project",
        due_date=due,
        max_score=100.0,
        status="submitted",
    )
    assert assignment.status == "submitted"
    assert assignment.title == "Final Research Project"
