"""The Ryot integration: workouts and body measurements as sensors."""

from __future__ import annotations

from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME, CONF_VERIFY_SSL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import RyotClient
from .const import CONF_API_TOKEN
from .coordinator import RyotConfigEntry, RyotCoordinator

PLATFORMS = [Platform.SENSOR]


def client_from_config(hass: HomeAssistant, data: dict) -> RyotClient:
    return RyotClient(
        async_get_clientsession(hass, verify_ssl=data.get(CONF_VERIFY_SSL, True)),
        data[CONF_URL],
        api_token=data.get(CONF_API_TOKEN),
        username=data.get(CONF_USERNAME),
        password=data.get(CONF_PASSWORD),
    )


async def async_setup_entry(hass: HomeAssistant, entry: RyotConfigEntry) -> bool:
    coordinator = RyotCoordinator(hass, entry, client_from_config(hass, dict(entry.data)))
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: RyotConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
