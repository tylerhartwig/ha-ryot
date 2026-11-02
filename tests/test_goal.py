"""Weekly goal / streak / history maths (pure, fixed dates)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from homeassistant.util import dt as dt_util

from custom_components.ryot.api import Workout
from custom_components.ryot.coordinator import goal_stats

# A Wednesday. Its week starts Monday 2026-10-26.
TODAY = date(2026, 10, 28)


def _w(day: date, minutes: int = 60, volume: float = 1000) -> Workout:
    start = dt_util.as_utc(datetime.combine(day, time(18), tzinfo=dt_util.get_default_time_zone()))
    return Workout(id=str(day), name="w", start=start, end=start + timedelta(minutes=minutes),
                   duration_s=minutes * 60, calories=0, reps=0, volume=volume, distance=0, personal_bests=0)


def _week(monday: date, n: int) -> list[Workout]:
    return [_w(monday + timedelta(days=i)) for i in range(n)]


def test_streak_counts_back_from_last_week_while_current_in_progress():
    mon = date(2026, 10, 26)
    workouts = _week(mon, 1) + _week(mon - timedelta(weeks=1), 3) + _week(mon - timedelta(weeks=2), 4) \
        + _week(mon - timedelta(weeks=3), 2) + _week(mon - timedelta(weeks=4), 3)
    g = goal_stats(workouts, 3, TODAY)
    assert (g.this_week, g.streak, g.best_streak) == (1, 2, 2)


def test_current_week_counts_once_goal_met():
    mon = date(2026, 10, 26)
    workouts = _week(mon, 3) + _week(mon - timedelta(weeks=1), 3)
    g = goal_stats(workouts, 3, TODAY)
    assert (g.this_week, g.streak, g.best_streak) == (3, 2, 2)


def test_missed_last_week_breaks_streak_but_best_remembers():
    mon = date(2026, 10, 26)
    workouts = _week(mon - timedelta(weeks=1), 2) + [w for k in range(2, 7) for w in _week(mon - timedelta(weeks=k), 3)]
    g = goal_stats(workouts, 3, TODAY)
    assert (g.streak, g.best_streak) == (0, 5)


def test_week_history_and_months():
    mon = date(2026, 10, 26)
    workouts = _week(mon, 2) + [_w(date(2026, 8, 3), minutes=30, volume=500)]
    g = goal_stats(workouts, 3, TODAY)
    assert len(g.weeks) == 52
    assert g.weeks[-1] == {"week": "2026-10-26", "workouts": 2, "minutes": 120, "volume": 2000}
    assert g.weeks[-2]["workouts"] == 0
    assert [m["month"] for m in g.months] == ["2026-08", "2026-09", "2026-10"]
    assert [m["workouts"] for m in g.months] == [1, 0, 2]
    assert g.this_month == 2
    assert g.days_since_last == 1  # Tuesday's workout


def test_no_workouts():
    g = goal_stats([], 3, TODAY)
    assert (g.this_week, g.streak, g.best_streak, g.days_since_last, g.months) == (0, 0, 0, None, [])
    assert all(w["workouts"] == 0 for w in g.weeks)


def test_month_rollover_across_year():
    g = goal_stats([_w(date(2025, 11, 30))], 3, date(2026, 2, 2))
    assert [m["month"] for m in g.months] == ["2025-11", "2025-12", "2026-01", "2026-02"]
