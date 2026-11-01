"""Polls Ryot and derives the fitness summary the sensors expose."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import Measurement, RyotAuthError, RyotClient, RyotError, RyotUser, Workout, window_start
from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
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
            active_days=len({dt_util.as_local(w.start).date() for w in workouts}),
        )


@dataclass(slots=True)
class RyotData:
    user: RyotUser
    last_workout: Workout | None
    total_workouts: int
    short: WindowStats
    long: WindowStats
    measurements: dict[str, Measurement]


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

    async def _async_update_data(self) -> RyotData:
        now = dt_util.utcnow()
        try:
            user = await self.client.get_user()
            recent, total = await self.client.get_recent_workouts(window_start(now, WINDOW_LONG_DAYS))
            measurements = await self.client.get_latest_measurements(window_start(now, MEASUREMENT_LOOKBACK_DAYS))
        except RyotAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except RyotError as err:
            raise UpdateFailed(str(err)) from err

        short_since = window_start(now, WINDOW_SHORT_DAYS)
        long_since = window_start(now, WINDOW_LONG_DAYS)
        return RyotData(
            user=user,
            last_workout=recent[0] if recent else None,
            total_workouts=total,
            short=WindowStats.of([w for w in recent if w.start >= short_since]),
            long=WindowStats.of([w for w in recent if w.start >= long_since]),
            measurements=measurements,
        )
