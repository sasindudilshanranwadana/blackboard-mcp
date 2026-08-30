from blackboard.models import GradeEntry


def test_grade_standing_bands():
    # Test calculation logic for grading bands
    scores = [
        GradeEntry(column_name="Ass1", score=88.0, max_score=100.0),
        GradeEntry(column_name="Ass2", score=76.0, max_score=100.0),
    ]
    total_earned = sum(g.score for g in scores if g.score is not None)
    total_max = sum(g.max_score for g in scores if g.max_score is not None)
    overall_pct = round((total_earned / total_max) * 100, 1)

    assert overall_pct == 82.0
    # 82.0% falls in Distinction (D) band (75-84%)
    assert 75.0 <= overall_pct < 85.0
