from datetime import datetime, timezone
import pytest
from blackboard.client import _parse_bb_datetime
from server import _fmt_dt, _urgency_emoji


def test_parse_bb_datetime_valid():
    dt_str = "2026-09-01T14:30:00.000Z"
    parsed = _parse_bb_datetime(dt_str)
    assert parsed is not None
    assert parsed.year == 2026
    assert parsed.month == 9
    assert parsed.day == 1
    assert parsed.tzinfo == timezone.utc


def test_parse_bb_datetime_none_or_empty():
    assert _parse_bb_datetime(None) is None
    assert _parse_bb_datetime("") is None


def test_parse_bb_datetime_invalid():
    assert _parse_bb_datetime("invalid-date-string") is None


def test_fmt_dt():
    dt = datetime(2026, 9, 1, 14, 30, tzinfo=timezone.utc)
    formatted = _fmt_dt(dt)
    assert "01 Sep 2026" in formatted


def test_urgency_emoji():
    past_dt = datetime(2020, 1, 1, tzinfo=timezone.utc)
    assert _urgency_emoji(past_dt) == "⚫"
