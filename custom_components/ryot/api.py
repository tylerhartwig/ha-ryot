"""Minimal async client for Ryot's GraphQL API."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import aiohttp

GRAPHQL_PATH = "/backend/graphql"
WORKOUT_PAGE_SIZE = 20

_LOGIN = """
mutation($input: AuthUserInput!) {
  loginUser(input: $input) {
    __typename
    ... on LoginError { error }
    ... on ApiKeyResponse { apiKey }
  }
}"""

_USER_DETAILS = """
{
  userDetails {
    __typename
    ... on UserDetails {
      id
      name
      preferences {
        fitness {
          exercises { unitSystem }
          measurements { statistics { name unit } }
        }
      }
    }
  }
}"""

_WORKOUTS_LIST = """
query($input: UserTemplatesOrWorkoutsListInput!) {
  userWorkoutsList(input: $input) {
    response { items details { totalItems nextPage } }
  }
}"""

_WORKOUT_FIELDS = """
response {
  details {
    id name startTime endTime duration caloriesBurnt
    summary {
      total { reps weight distance duration restTime personalBestsAchieved }
      exercises { id numSets }
      focused { muscles { muscle } }
    }
  }
}"""

_MEASUREMENTS = """
query($input: UserMeasurementsListInput!) {
  userMeasurementsList(input: $input) {
    response { timestamp information { statistics { name value } } }
  }
}"""


class RyotError(Exception):
    """Generic Ryot API failure."""


class RyotConnectionError(RyotError):
    """Ryot could not be reached."""


class RyotAuthError(RyotError):
    """Credentials were rejected or the session expired."""


def _num(value: Any) -> float:
    """Ryot sends Decimals as strings."""
    if value is None:
        return 0.0
    try:
        return float(Decimal(str(value)))
    except InvalidOperation:
        return 0.0


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


@dataclass(slots=True)
class RyotUser:
    id: str
    name: str
    unit_system: str  # "METRIC" | "IMPERIAL"
    # Units the user configured per measurement statistic (often unset).
    measurement_units: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class Workout:
    id: str
    name: str
    start: datetime
    end: datetime
    duration_s: int
    calories: float
    reps: float
    volume: float
    distance: float
    personal_bests: int
    exercises: list[str] = field(default_factory=list)
    muscles: list[str] = field(default_factory=list)

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> Workout:
        summary = d.get("summary") or {}
        total = summary.get("total") or {}
        muscles = (summary.get("focused") or {}).get("muscles") or []
        start, end = _dt(d["startTime"]), _dt(d["endTime"])
        return cls(
            id=d["id"],
            name=d["name"],
            start=start,
            end=end,
            duration_s=int(d.get("duration") or (end - start).total_seconds()),
            calories=_num(d.get("caloriesBurnt")),
            reps=_num(total.get("reps")),
            volume=_num(total.get("weight")),
            distance=_num(total.get("distance")),
            personal_bests=int(total.get("personalBestsAchieved") or 0),
            exercises=[e["id"] for e in summary.get("exercises") or []],
            muscles=[m["muscle"].lower() for m in muscles],
        )


@dataclass(slots=True)
class Measurement:
    name: str
    value: float
    timestamp: datetime


class RyotClient:
    """Talks to one Ryot instance as one user.

    Auth is either a fixed API token, or username/password, in which case the
    client logs in on demand and again whenever the session is rejected.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        *,
        api_token: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self._session = session
        self._endpoint = url.rstrip("/") + GRAPHQL_PATH
        self._token = api_token or None
        self._username = username or None
        self._password = password or None
        if not self._token and not (self._username and self._password):
            raise ValueError("an API token or username and password is required")

    @property
    def _can_login(self) -> bool:
        return bool(self._username and self._password)

    async def _post(self, query: str, variables: dict[str, Any] | None, token: str | None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            async with self._session.post(
                self._endpoint,
                json={"query": query, "variables": variables or {}},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status in (401, 403):
                    raise RyotAuthError(f"HTTP {resp.status}")
                if resp.status != 200:
                    raise RyotError(f"HTTP {resp.status} from {self._endpoint}")
                body = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise RyotConnectionError(str(err)) from err
        except ValueError as err:  # not JSON: wrong URL (e.g. the frontend)
            raise RyotError(f"{self._endpoint} did not return JSON") from err
        if errors := body.get("errors"):
            msg = "; ".join(e.get("message", "?") for e in errors)
            if "session" in msg.lower() or "unauthor" in msg.lower():
                raise RyotAuthError(msg)
            raise RyotError(msg)
        return body["data"]

    async def login(self) -> str:
        data = await self._post(
            _LOGIN,
            {"input": {"password": {"username": self._username, "password": self._password}}},
            None,
        )
        result = data["loginUser"]
        if result["__typename"] != "ApiKeyResponse":
            raise RyotAuthError(result.get("error", "login failed"))
        self._token = result["apiKey"]
        return self._token

    async def query(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run an authenticated query, re-logging in once if allowed."""
        if not self._token:
            await self.login()
        try:
            return await self._post(query, variables, self._token)
        except RyotAuthError:
            if not self._can_login:
                raise
            await self.login()
            return await self._post(query, variables, self._token)

    async def _user_details(self) -> dict[str, Any]:
        details = (await self._post(_USER_DETAILS, None, self._token))["userDetails"]
        if details["__typename"] != "UserDetails":
            raise RyotAuthError("session invalid")
        return details

    async def get_user(self) -> RyotUser:
        # userDetails reports a bad session as data, not as an error, so the
        # re-login retry is done here rather than in query().
        if not self._token:
            await self.login()
        try:
            details = await self._user_details()
        except RyotAuthError:
            if not self._can_login:
                raise
            await self.login()
            details = await self._user_details()
        fitness = details["preferences"]["fitness"]
        return RyotUser(
            id=details["id"],
            name=details["name"],
            unit_system=fitness["exercises"]["unitSystem"],
            measurement_units={
                s["name"]: s["unit"] for s in fitness["measurements"]["statistics"] if s.get("unit")
            },
        )

    async def get_recent_workouts(self, since: datetime) -> tuple[list[Workout], int]:
        """Workouts started at or after `since` (newest first), always including
        the most recent one, plus the all-time workout count."""
        workouts: list[Workout] = []
        total = 0
        page: int | None = 1
        while page:
            data = await self.query(
                _WORKOUTS_LIST,
                {"input": {"search": {"page": page, "take": WORKOUT_PAGE_SIZE}, "sort": {"by": "TIME", "order": "DESC"}}},
            )
            resp = data["userWorkoutsList"]["response"]
            total = resp["details"]["totalItems"]
            ids: list[str] = resp["items"]
            if not ids:
                break
            batch = await self._workout_details(ids)
            for w in batch:
                if workouts and w.start < since:
                    return workouts, total
                workouts.append(w)
            page = resp["details"].get("nextPage")
        return workouts, total

    async def _workout_details(self, ids: list[str]) -> list[Workout]:
        # One request for the whole page via aliases.
        params = ", ".join(f"$w{i}: String!" for i in range(len(ids)))
        fields = "\n".join(
            f"w{i}: userWorkoutDetails(workoutId: $w{i}) {{ {_WORKOUT_FIELDS} }}" for i in range(len(ids))
        )
        data = await self.query(f"query({params}) {{ {fields} }}", {f"w{i}": wid for i, wid in enumerate(ids)})
        return [Workout.from_api(data[f"w{i}"]["response"]["details"]) for i in range(len(ids))]

    async def get_latest_measurements(self, since: datetime) -> dict[str, Measurement]:
        """Latest value per measurement statistic (e.g. weight, body_fat)."""
        data = await self.query(_MEASUREMENTS, {"input": {"startTime": since.astimezone(timezone.utc).isoformat()}})
        latest: dict[str, Measurement] = {}
        for item in data["userMeasurementsList"]["response"]:
            ts = _dt(item["timestamp"])
            for stat in item["information"]["statistics"]:
                name = stat["name"]
                if name not in latest or ts >= latest[name].timestamp:
                    latest[name] = Measurement(name=name, value=_num(stat["value"]), timestamp=ts)
        return latest


def window_start(now: datetime, days: int) -> datetime:
    return now - timedelta(days=days)
