"""Polls Ryot and derives the fitness summary the sensors expose."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import Measurement, RyotAuthError, RyotClient, RyotError, RyotUser, Workout, window_start
from .const import (
    CONF_WEEKLY_GOAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_WEEKLY_GOAL,
    DOMAIN,
    FULL_REFRESH_INTERVAL,
    HISTORY_WEEKS,
    MEASUREMENT_LOOKBACK_DAYS,
    WINDOW_LONG_DAYS,
    WINDOW_SHORT_DAYS,
)

_LOGGER = logging.getLogger(__name__)

type RyotConfigEntry = ConfigEntry[RyotCoordinator]


@dataclass(slots=True)
class WindowStats:
    workouts: int
    minutes: float
    volume: float
    reps: float
    distance: float
    calories: float
    personal_bests: int
    active_days: int

    @classmethod
    def of(cls, workouts: list[Workout]) -> WindowStats:
        return cls(
            workouts=len(workouts),
            minutes=round(sum(w.duration_s for w in workouts) / 60, 1),
            volume=round(sum(w.volume for w in workouts), 1),
            reps=sum(w.reps for w in workouts),
            distance=round(sum(w.distance for w in workouts), 2),
            calories=round(sum(w.calories for w in workouts)),
            personal_bests=sum(w.personal_bests for w in workouts),
            active_days=len({_local_day(w) for w in workouts}),
        )


@dataclass(slots=True)
class GoalStats:
    """Progress against the weekly workout goal (weeks start Monday, local time)."""

    goal: int
    this_week: int
    this_month: int
    streak: int  # consecutive weeks meeting the goal; the current week counts once met
    best_streak: int
    days_since_last: int | None
    weeks: list[dict]  # oldest first, last HISTORY_WEEKS weeks incl. the current one
    months: list[dict]  # oldest first, every month since the first workout


@dataclass(slots=True)
class RyotData:
    user: RyotUser
    last_workout: Workout | None
    total_workouts: int
    short: WindowStats
    long: WindowStats
    goal: GoalStats
    measurements: dict[str, Measurement]


def _local_day(w: Workout) -> date:
    return dt_util.as_local(w.start).date()


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def goal_stats(workouts: list[Workout], goal: int, today: date) -> GoalStats:
    by_week: dict[date, list[Workout]] = {}
    by_month: dict[str, list[Workout]] = {}
    for w in workouts:
        day = _local_day(w)
        by_week.setdefault(_week_start(day), []).append(w)
        by_month.setdefault(day.strftime("%Y-%m"), []).append(w)

    this_week = _week_start(today)
    counts = Counter({wk: len(ws) for wk, ws in by_week.items()})

    # Best streak over all completed (and the current, if already met) weeks.
    best = run = 0
    if counts:
        wk = min(counts)
        while wk <= this_week:
            if counts[wk] >= goal:
                run += 1
                best = max(best, run)
            elif wk != this_week:
                run = 0
            wk += timedelta(weeks=1)

    # Current streak: an unfinished current week doesn't break it.
    streak = 0
    wk = this_week if counts[this_week] >= goal else this_week - timedelta(weeks=1)
    while counts[wk] >= goal:
        streak += 1
        wk -= timedelta(weeks=1)

    weeks = []
    for i in range(HISTORY_WEEKS - 1, -1, -1):
        wk = this_week - timedelta(weeks=i)
        ws = by_week.get(wk, [])
        weeks.append({
            "week": wk.isoformat(),
            "workouts": len(ws),
            "minutes": round(sum(w.duration_s for w in ws) / 60),
            "volume": round(sum(w.volume for w in ws)),
        })

    months = []
    if by_month:
        m = date.fromisoformat(min(by_month) + "-01")
        while m <= today:
            key = m.strftime("%Y-%m")
            ws = by_month.get(key, [])
            months.append({
                "month": key,
                "workouts": len(ws),
                "minutes": round(sum(w.duration_s for w in ws) / 60),
                "volume": round(sum(w.volume for w in ws)),
            })
            m = (m.replace(day=28) + timedelta(days=4)).replace(day=1)

    last_day = max((_local_day(w) for w in workouts), default=None)
    return GoalStats(
        goal=goal,
        this_week=counts[this_week],
        this_month=len(by_month.get(today.strftime("%Y-%m"), [])),
        streak=streak,
        best_streak=best,
        days_since_last=(today - last_day).days if last_day else None,
        weeks=weeks,
        months=months,
    )


class RyotCoordinator(DataUpdateCoordinator[RyotData]):
    config_entry: RyotConfigEntry

    def __init__(self, hass: HomeAssistant, entry: RyotConfigEntry, client: RyotClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.client = client
        # Full history, newest first. Refetched whole once a day (to catch edits
        # and deletions of old workouts); otherwise only the recent window.
        self._history: list[Workout] = []
        self._history_at: datetime | None = None

    async def _fetch_workouts(self, now: datetime) -> tuple[list[Workout], int]:
        if self._history_at is None or now - self._history_at >= FULL_REFRESH_INTERVAL:
            self._history, total = await self.client.get_recent_workouts(None)
            self._history_at = now
            return self._history, total
        since = window_start(now, WINDOW_LONG_DAYS)
        recent, total = await self.client.get_recent_workouts(since)
        cutoff = min((w.start for w in recent), default=since)
        self._history = recent + [w for w in self._history if w.start < min(cutoff, since)]
        return self._history, total

    async def _async_update_data(self) -> RyotData:
        now = dt_util.utcnow()
        try:
            user = await self.client.get_user()
            workouts, total = await self._fetch_workouts(now)
            measurements = await self.client.get_latest_measurements(window_start(now, MEASUREMENT_LOOKBACK_DAYS))
        except RyotAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except RyotError as err:
            raise UpdateFailed(str(err)) from err

        short_since = window_start(now, WINDOW_SHORT_DAYS)
        long_since = window_start(now, WINDOW_LONG_DAYS)
        goal = self.config_entry.options.get(CONF_WEEKLY_GOAL, DEFAULT_WEEKLY_GOAL)
        return RyotData(
            user=user,
            last_workout=workouts[0] if workouts else None,
            total_workouts=total,
            short=WindowStats.of([w for w in workouts if w.start >= short_since]),
            long=WindowStats.of([w for w in workouts if w.start >= long_since]),
            goal=goal_stats(workouts, goal, dt_util.as_local(now).date()),
            measurements=measurements,
        )
