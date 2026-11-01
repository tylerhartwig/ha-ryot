"""RyotClient against the isolated Ryot."""

from __future__ import annotations

from datetime import timedelta

from aiohttp import ClientSession
import pytest

from custom_components.ryot.api import RyotAuthError, RyotClient, RyotConnectionError, RyotError
from homeassistant.util import dt as dt_util

from .ryot_seed import add_workout


async def test_token_auth_reads_user(seeded_user):
    async with ClientSession() as s:
        user = await RyotClient(s, seeded_user.url, api_token=seeded_user.token).get_user()
    assert (user.id, user.name, user.unit_system) == (seeded_user.id, seeded_user.username, "METRIC")


async def test_password_auth_and_relogin_after_expired_session(seeded_user):
    async with ClientSession() as s:
        client = RyotClient(s, seeded_user.url, username=seeded_user.username, password=seeded_user.password)
        assert (await client.get_user()).id == seeded_user.id
        client._token = "expired-or-revoked"
        assert (await client.get_user()).id == seeded_user.id
        assert client._token != "expired-or-revoked"


async def test_bad_token_and_bad_password(ryot_user):
    async with ClientSession() as s:
        with pytest.raises(RyotAuthError):
            await RyotClient(s, ryot_user.url, api_token="nope").get_user()
        with pytest.raises(RyotAuthError):
            await RyotClient(s, ryot_user.url, username=ryot_user.username, password="wrong").get_user()


async def test_wrong_urls(ryot_user):
    async with ClientSession() as s:
        with pytest.raises(RyotConnectionError):
            await RyotClient(s, "http://127.0.0.1:1", api_token="x").get_user()
        # Something that isn't Ryot's GraphQL (path not proxied -> 404).
        with pytest.raises(RyotError):
            await RyotClient(s, ryot_user.url + "/elsewhere", api_token="x").get_user()


async def test_recent_workouts_window_and_order(seeded_user):
    async with ClientSession() as s:
        client = RyotClient(s, seeded_user.url, api_token=seeded_user.token)
        workouts, total = await client.get_recent_workouts(dt_util.utcnow() - timedelta(days=30))
    assert total == 4
    assert [w.name for w in workouts] == ["Push day", "Pull day", "Older"]
    push = workouts[0]
    assert push.duration_s == 45 * 60
    assert push.volume == 8 * 60 + 8 * 62.5
    assert push.reps == 16
    assert push.calories == 320
    assert push.exercises and push.muscles


async def test_last_workout_returned_even_if_old(ryot_user):
    add_workout(ryot_user, days_ago=90, minutes=20, sets=[(5, 20)], name="Long ago")
    async with ClientSession() as s:
        workouts, total = await RyotClient(s, ryot_user.url, api_token=ryot_user.token).get_recent_workouts(
            dt_util.utcnow() - timedelta(days=30)
        )
    assert total == 1
    assert [w.name for w in workouts] == ["Long ago"]


async def test_pagination(ryot_user):
    for i in range(23):  # page size is 20
        add_workout(ryot_user, days_ago=0.5 + i * 0.1, minutes=10, sets=[(1, 1)], name=f"w{i}")
    async with ClientSession() as s:
        workouts, total = await RyotClient(s, ryot_user.url, api_token=ryot_user.token).get_recent_workouts(
            dt_util.utcnow() - timedelta(days=30)
        )
    assert total == 23
    assert [w.name for w in workouts] == [f"w{i}" for i in range(23)]


async def test_latest_measurements(seeded_user):
    async with ClientSession() as s:
        m = await RyotClient(s, seeded_user.url, api_token=seeded_user.token).get_latest_measurements(
            dt_util.utcnow() - timedelta(days=365)
        )
    assert {k: v.value for k, v in m.items()} == {"weight": 81.4, "body_fat": 18.5}


async def test_no_data(ryot_user):
    async with ClientSession() as s:
        client = RyotClient(s, ryot_user.url, api_token=ryot_user.token)
        assert await client.get_recent_workouts(dt_util.utcnow() - timedelta(days=30)) == ([], 0)
        assert await client.get_latest_measurements(dt_util.utcnow() - timedelta(days=365)) == {}
