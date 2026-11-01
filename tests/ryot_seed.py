"""Synchronous helpers that create users and data on the isolated Ryot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import secrets
import time
import urllib.request

EMPTY_ASSETS = {"s3Images": [], "s3Videos": [], "remoteImages": [], "remoteVideos": []}


@dataclass
class SeededUser:
    url: str
    username: str
    password: str
    token: str
    id: str


def gql(url: str, query: str, variables: dict | None = None, token: str | None = None) -> dict:
    headers = {"content-type": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"{url}/backend/graphql", json.dumps({"query": query, "variables": variables or {}}).encode(), headers
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.load(r)
    if body.get("errors"):
        raise RuntimeError(body["errors"])
    return body["data"]


def create_user(url: str, admin_token: str) -> SeededUser:
    username, password = f"t_{secrets.token_hex(4)}", secrets.token_hex(12)
    cred = {"password": {"username": username, "password": password}}
    reg = gql(url, "mutation($i:RegisterUserInput!){registerUser(input:$i){__typename ... on RegisterError{error} ... on StringIdObject{id}}}",
              {"i": {"data": cred, "adminAccessToken": admin_token}})["registerUser"]
    assert reg["__typename"] == "StringIdObject", reg
    login = gql(url, "mutation($i:AuthUserInput!){loginUser(input:$i){... on ApiKeyResponse{apiKey}}}", {"i": cred})["loginUser"]
    token = gql(url, "mutation{generateAuthToken}", token=login["apiKey"])["generateAuthToken"]
    return SeededUser(url, username, password, token, reg["id"])


def exercise_id(url: str, token: str, search: str = "bench press") -> str:
    # The exercise library is downloaded in the background right after boot.
    for _ in range(120):
        items = gql(url, "query($i:UserExercisesListInput!){userExercisesList(input:$i){response{items}}}",
                    {"i": {"search": {"query": search, "take": 1}}}, token)["userExercisesList"]["response"]["items"]
        if items:
            return items[0]
        time.sleep(1)
    raise RuntimeError("Ryot exercise library never loaded")


def add_workout(user: SeededUser, *, days_ago: float, minutes: int, sets: list[tuple[int, float]],
                calories: int | None = None, name: str = "Workout") -> str:
    end = datetime.now(timezone.utc) - timedelta(days=days_ago)
    start = end - timedelta(minutes=minutes)
    workout = {
        "name": name,
        "startTime": start.isoformat(),
        "endTime": end.isoformat(),
        "supersets": [],
        "exercises": [{
            "exerciseId": exercise_id(user.url, user.token),
            "notes": [],
            "unitSystem": "METRIC",
            "sets": [{"lot": "NORMAL", "statistic": {"reps": str(r), "weight": str(w)}, "confirmedAt": end.isoformat()}
                     for r, w in sets],
        }],
    }
    if calories is not None:
        workout["caloriesBurnt"] = str(calories)
    return gql(user.url, "mutation($i:UserWorkoutInput!){createOrUpdateUserWorkout(input:$i)}", {"i": workout}, user.token)[
        "createOrUpdateUserWorkout"
    ]


def add_measurement(user: SeededUser, *, days_ago: float, **stats: float) -> None:
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    gql(user.url, "mutation($i:UserMeasurementInput!){createOrUpdateUserMeasurement(input:$i)}",
        {"i": {"timestamp": ts, "information": {"assets": EMPTY_ASSETS,
               "statistics": [{"name": k, "value": str(v)} for k, v in stats.items()]}}}, user.token)


def seed_standard(user: SeededUser) -> None:
    """Known data set the sensor tests assert against.

    7d window: 2 workouts, 75 min, volume 980+1500=2480, 520 kcal
    30d window: +1 workout (60 min, volume 1000, no calories) -> 3, 135 min, 3480
    outside: one 40 days ago. Total 4.
    Inserted out of order to make sure sorting is by time, not insertion.
    """
    add_workout(user, days_ago=10, minutes=60, sets=[(10, 50), (10, 50)], name="Older")
    add_workout(user, days_ago=1, minutes=45, sets=[(8, 60), (8, 62.5)], calories=320, name="Push day")
    add_workout(user, days_ago=40, minutes=30, sets=[(5, 100)], calories=100, name="Ancient")
    add_workout(user, days_ago=3, minutes=30, sets=[(10, 50), (10, 50), (10, 50)], calories=200, name="Pull day")
    add_measurement(user, days_ago=5, weight=82.0)
    add_measurement(user, days_ago=1, weight=81.4)
    add_measurement(user, days_ago=2, body_fat=18.5)
