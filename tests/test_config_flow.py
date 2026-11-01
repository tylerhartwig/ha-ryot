"""Config flow against the isolated Ryot."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.ryot.const import CONF_API_TOKEN, DOMAIN


async def _start(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    return result["flow_id"]


async def test_password_login_creates_entry(hass: HomeAssistant, ryot_user):
    result = await hass.config_entries.flow.async_configure(
        await _start(hass),
        {CONF_URL: ryot_user.url + "/", CONF_USERNAME: ryot_user.username, CONF_PASSWORD: ryot_user.password,
         CONF_API_TOKEN: "ignored-when-password-given", CONF_VERIFY_SSL: True},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"Ryot ({ryot_user.username})"
    assert result["data"] == {CONF_URL: ryot_user.url, CONF_USERNAME: ryot_user.username,
                              CONF_PASSWORD: ryot_user.password, CONF_VERIFY_SSL: True}
    assert result["result"].unique_id == f"{ryot_user.url}#{ryot_user.id}"


async def test_token_creates_entry_and_duplicate_aborts(hass: HomeAssistant, ryot_user):
    data = {CONF_URL: ryot_user.url, CONF_API_TOKEN: ryot_user.token}
    result = await hass.config_entries.flow.async_configure(await _start(hass), data)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_USERNAME not in result["data"]

    # Same Ryot user via password -> already configured.
    result = await hass.config_entries.flow.async_configure(
        await _start(hass), {CONF_URL: ryot_user.url, CONF_USERNAME: ryot_user.username, CONF_PASSWORD: ryot_user.password}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_errors(hass: HomeAssistant, ryot_user):
    flow = await _start(hass)
    cases = [
        ({CONF_URL: ryot_user.url}, "auth_missing"),
        ({CONF_URL: ryot_user.url, CONF_USERNAME: ryot_user.username}, "auth_missing"),
        ({CONF_URL: ryot_user.url, CONF_API_TOKEN: "bad"}, "invalid_auth"),
        ({CONF_URL: ryot_user.url, CONF_USERNAME: ryot_user.username, CONF_PASSWORD: "bad"}, "invalid_auth"),
        ({CONF_URL: "http://127.0.0.1:1", CONF_API_TOKEN: "x"}, "cannot_connect"),
        ({CONF_URL: ryot_user.url + "/nope", CONF_API_TOKEN: "x"}, "not_ryot"),
    ]
    for data, error in cases:
        result = await hass.config_entries.flow.async_configure(flow, data)
        assert result["type"] is FlowResultType.FORM, data
        assert result["errors"] == {"base": error}, data
    # Recovers after errors.
    result = await hass.config_entries.flow.async_configure(flow, {CONF_URL: ryot_user.url, CONF_API_TOKEN: ryot_user.token})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_reauth_switches_token_to_password(hass: HomeAssistant, ryot_user, ryot_stack):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from .ryot_seed import create_user

    entry = MockConfigEntry(domain=DOMAIN, unique_id=f"{ryot_user.url}#{ryot_user.id}",
                            data={CONF_URL: ryot_user.url, CONF_API_TOKEN: "expired"})
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    other = create_user(ryot_stack["url"], ryot_stack["admin_token"])
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_TOKEN: other.token})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: ryot_user.username, CONF_PASSWORD: ryot_user.password}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data == {CONF_URL: ryot_user.url, CONF_VERIFY_SSL: True,
                          CONF_USERNAME: ryot_user.username, CONF_PASSWORD: ryot_user.password}
