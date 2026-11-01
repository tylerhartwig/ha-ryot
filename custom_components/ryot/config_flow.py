"""Config flow for Ryot."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from . import client_from_config
from .api import RyotAuthError, RyotConnectionError, RyotError, RyotUser
from .const import CONF_API_TOKEN, DOMAIN

_LOGGER = logging.getLogger(__name__)

_SECRET = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
_AUTH_FIELDS = {
    vol.Optional(CONF_USERNAME): str,
    vol.Optional(CONF_PASSWORD): _SECRET,
    vol.Optional(CONF_API_TOKEN): _SECRET,
}
USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
        **_AUTH_FIELDS,
        vol.Optional(CONF_VERIFY_SSL, default=True): bool,
    }
)


def _clean(data: Mapping[str, Any]) -> dict[str, Any]:
    """Keep exactly one auth method: username/password wins over a token."""
    out = {k: v for k, v in data.items() if v not in (None, "")}
    if out.get(CONF_USERNAME) and out.get(CONF_PASSWORD):
        out.pop(CONF_API_TOKEN, None)
    else:
        out.pop(CONF_USERNAME, None)
        out.pop(CONF_PASSWORD, None)
    return out


class RyotConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _validate(self, data: dict[str, Any]) -> tuple[RyotUser | None, dict[str, str]]:
        if not data.get(CONF_API_TOKEN) and not data.get(CONF_USERNAME):
            return None, {"base": "auth_missing"}
        try:
            return await client_from_config(self.hass, data).get_user(), {}
        except RyotAuthError:
            return None, {"base": "invalid_auth"}
        except RyotConnectionError:
            return None, {"base": "cannot_connect"}
        except RyotError:
            _LOGGER.exception("Unexpected response from Ryot")
            return None, {"base": "not_ryot"}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _clean(user_input)
            data[CONF_URL] = data[CONF_URL].rstrip("/")
            user, errors = await self._validate(data)
            if user:
                await self.async_set_unique_id(f"{data[CONF_URL]}#{user.id}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=f"Ryot ({user.name})", data=data)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _clean({CONF_URL: entry.data[CONF_URL], CONF_VERIFY_SSL: entry.data.get(CONF_VERIFY_SSL, True), **user_input})
            user, errors = await self._validate(data)
            if user:
                await self.async_set_unique_id(f"{data[CONF_URL]}#{user.id}")
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(_AUTH_FIELDS),
            description_placeholders={"url": entry.data[CONF_URL]},
            errors=errors,
        )
