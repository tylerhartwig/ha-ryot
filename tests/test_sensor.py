"""Full setup in the in-process HA against the isolated Ryot."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ryot.const import CONF_API_TOKEN, DOMAIN

from .ryot_seed import add_measurement, add_workout


async def _setup(hass: HomeAssistant, user, **data) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title=f"Ryot ({user.username})", unique_id=f"{user.url}#{user.id}",
                            data={CONF_URL: user.url, **(data or {CONF_API_TOKEN: user.token})})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _eid(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> str:
    eid = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.unique_id}_{key}")
    assert eid, key
    return eid


def _state(hass, entry, key):
    return hass.states.get(_eid(hass, entry, key))


async def test_sensors_from_seeded_data(hass: HomeAssistant, seeded_user):
    entry = await _setup(hass, seeded_user)
    assert entry.state is ConfigEntryState.LOADED

    last = _state(hass, entry, "last_workout")
    assert last.attributes["device_class"] == "timestamp"
    assert abs((dt_util.parse_datetime(last.state) - dt_util.utcnow()).total_seconds() - -86400) < 600
    assert last.attributes["name"] == "Push day"
    assert last.attributes["volume"] == 980
    assert last.attributes["muscles"]

    expect = {
        "last_workout_name": ("Push day", None),
        "last_workout_duration": ("45.0", "min"),
        "last_workout_volume": ("980.0", "kg"),
        "total_workouts": ("4", None),
        "workouts_7d": ("2", None),
        "active_days_7d": ("2", None),
        "minutes_7d": ("75.0", "min"),
        "volume_7d": ("2480.0", "kg"),
        "workouts_30d": ("3", None),
        "minutes_30d": ("135.0", "min"),
        "volume_30d": ("3480.0", "kg"),
        "measurement_weight": ("81.4", "kg"),
        "measurement_body_fat": ("18.5", "%"),
    }
    for key, (state, unit) in expect.items():
        s = _state(hass, entry, key)
        assert (s.state, s.attributes.get("unit_of_measurement")) == (state, unit), key

    assert hass.states.get(_eid(hass, entry, "workouts_7d")).name.endswith("Workouts (7d)")
    assert hass.states.get(_eid(hass, entry, "measurement_body_fat")).name.endswith("Body fat")
    # Off by default.
    for key in ("reps_7d", "calories_7d", "last_workout_calories"):
        assert er.async_get(hass).async_get(_eid(hass, entry, key)).disabled_by is not None, key

    assert _state(hass, entry, "days_since_last_workout").state == "1"
    assert _state(hass, entry, "best_weekly_goal_streak").state == "0"  # never 3 in one week
    week = _state(hass, entry, "workouts_this_week")
    assert week.attributes["goal"] == 3
    assert len(week.attributes["weeks"]) == 52
    assert sum(w["workouts"] for w in week.attributes["weeks"]) == 4
    months = _state(hass, entry, "workouts_this_month").attributes["months"]
    assert sum(m["workouts"] for m in months) == 4
    assert _state(hass, entry, "weekly_goal_streak").attributes["unit_of_measurement"] == "w"


async def test_empty_account(hass: HomeAssistant, ryot_user):
    entry = await _setup(hass, ryot_user)
    assert _state(hass, entry, "last_workout").state == STATE_UNKNOWN
    assert _state(hass, entry, "workouts_7d").state == "0"
    assert _state(hass, entry, "total_workouts").state == "0"


async def test_refresh_picks_up_new_workout_and_measurement(hass: HomeAssistant, seeded_user):
    entry = await _setup(hass, seeded_user)
    assert er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.unique_id}_measurement_waist") is None

    add_workout(seeded_user, days_ago=0.01, minutes=20, sets=[(10, 10)], name="Just now")
    add_measurement(seeded_user, days_ago=0.01, waist=84)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert _state(hass, entry, "last_workout_name").state == "Just now"
    assert _state(hass, entry, "workouts_7d").state == "3"
    assert _state(hass, entry, "total_workouts").state == "5"
    assert _state(hass, entry, "measurement_waist").state == "84.0"


async def test_password_entry(hass: HomeAssistant, seeded_user):
    entry = await _setup(hass, seeded_user, **{CONF_USERNAME: seeded_user.username, CONF_PASSWORD: seeded_user.password})
    assert entry.state is ConfigEntryState.LOADED
    assert _state(hass, entry, "workouts_30d").state == "3"


async def test_bad_token_starts_reauth(hass: HomeAssistant, ryot_user):
    entry = await _setup(hass, ryot_user, **{CONF_API_TOKEN: "revoked"})
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_unreachable_retries(hass: HomeAssistant, ryot_user):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="x", data={CONF_URL: "http://127.0.0.1:1", CONF_API_TOKEN: "x"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_imperial_units(hass: HomeAssistant, seeded_user, monkeypatch):
    """Ryot reports values in the user's unit system; units must follow it."""
    from custom_components.ryot.api import RyotClient

    real = RyotClient.get_user

    async def imperial(self):
        user = await real(self)
        user.unit_system = "IMPERIAL"
        return user

    monkeypatch.setattr(RyotClient, "get_user", imperial)
    entry = await _setup(hass, seeded_user)
    assert _state(hass, entry, "volume_7d").attributes["unit_of_measurement"] == "lb"
    assert _state(hass, entry, "measurement_weight").attributes["unit_of_measurement"] == "lb"
    # HA must not have converted the number.
    assert _state(hass, entry, "volume_7d").state == "2480.0"


async def test_options_goal_and_incremental_history(hass: HomeAssistant, seeded_user):
    entry = await _setup(hass, seeded_user)
    # Goal 1/week: the seeded workouts sit in at least 3 distinct weeks.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"weekly_goal": 1})
    assert entry.options == {"weekly_goal": 1}
    await hass.async_block_till_done()
    assert _state(hass, entry, "workouts_this_week").attributes["goal"] == 1
    assert int(_state(hass, entry, "best_weekly_goal_streak").state) >= 1

    # A poll that isn't the daily full refresh keeps old history and adds new.
    coordinator = entry.runtime_data
    before = len(coordinator._history)
    add_workout(seeded_user, days_ago=0.01, minutes=20, sets=[(10, 10)], name="Just now")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert len(coordinator._history) == before + 1
    assert any(w.name == "Ancient" for w in coordinator._history)  # 40 days old, outside the window
    months = _state(hass, entry, "workouts_this_month").attributes["months"]
    assert sum(m["workouts"] for m in months) == 5
