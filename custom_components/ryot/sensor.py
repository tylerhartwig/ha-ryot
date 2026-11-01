"""Ryot sensors: last workout, rolling 7/30-day totals, latest body measurements."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfLength, UnitOfMass, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, WINDOW_LONG_DAYS, WINDOW_SHORT_DAYS
from .coordinator import RyotConfigEntry, RyotCoordinator, RyotData, WindowStats

PARALLEL_UPDATES = 0

KCAL = "kcal"


def _mass(data: RyotData) -> str:
    return UnitOfMass.POUNDS if data.user.unit_system == "IMPERIAL" else UnitOfMass.KILOGRAMS


def _length(data: RyotData) -> str:
    return UnitOfLength.MILES if data.user.unit_system == "IMPERIAL" else UnitOfLength.KILOMETERS


@dataclass(frozen=True, kw_only=True)
class RyotSensorDescription(SensorEntityDescription):
    value_fn: Callable[[RyotData], Any]
    unit_fn: Callable[[RyotData], str | None] | None = None
    attrs_fn: Callable[[RyotData], dict[str, Any] | None] | None = None


def _last(attr: str, scale: float = 1) -> Callable[[RyotData], Any]:
    def get(data: RyotData) -> Any:
        w = data.last_workout
        return None if w is None else round(getattr(w, attr) * scale, 1)

    return get


def _last_attrs(data: RyotData) -> dict[str, Any] | None:
    w = data.last_workout
    if w is None:
        return None
    return {
        "workout_id": w.id,
        "name": w.name,
        "started": w.start.isoformat(),
        "duration_min": round(w.duration_s / 60, 1),
        "exercises": w.exercises,
        "muscles": w.muscles,
        "reps": w.reps,
        "volume": w.volume,
        "distance": w.distance,
        "calories": w.calories,
        "personal_bests": w.personal_bests,
    }


def _window_sensors(key: str, days: int, pick: Callable[[RyotData], WindowStats]) -> list[RyotSensorDescription]:
    def d(stat: str, **kw: Any) -> RyotSensorDescription:
        return RyotSensorDescription(
            key=f"{stat}_{key}",
            translation_key=stat,
            translation_placeholders={"days": str(days)},
            state_class=SensorStateClass.MEASUREMENT,
            value_fn=lambda data: getattr(pick(data), stat),
            **kw,
        )

    return [
        d("workouts"),
        d("active_days"),
        d("minutes", native_unit_of_measurement=UnitOfTime.MINUTES, device_class=SensorDeviceClass.DURATION),
        d("volume", unit_fn=_mass, device_class=SensorDeviceClass.WEIGHT),
        d("reps", entity_registry_enabled_default=False),
        d("distance", unit_fn=_length, device_class=SensorDeviceClass.DISTANCE, entity_registry_enabled_default=False),
        d("calories", native_unit_of_measurement=KCAL),
        d("personal_bests"),
    ]


SENSORS: list[RyotSensorDescription] = [
    RyotSensorDescription(
        key="last_workout",
        translation_key="last_workout",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: data.last_workout.end if data.last_workout else None,
        attrs_fn=_last_attrs,
    ),
    RyotSensorDescription(
        key="last_workout_name",
        translation_key="last_workout_name",
        value_fn=lambda data: data.last_workout.name if data.last_workout else None,
    ),
    RyotSensorDescription(
        key="last_workout_duration",
        translation_key="last_workout_duration",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        value_fn=_last("duration_s", 1 / 60),
    ),
    RyotSensorDescription(
        key="last_workout_volume",
        translation_key="last_workout_volume",
        device_class=SensorDeviceClass.WEIGHT,
        unit_fn=_mass,
        value_fn=_last("volume"),
    ),
    RyotSensorDescription(
        key="last_workout_calories",
        translation_key="last_workout_calories",
        native_unit_of_measurement=KCAL,
        value_fn=_last("calories"),
    ),
    RyotSensorDescription(
        key="total_workouts",
        translation_key="total_workouts",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda data: data.total_workouts,
    ),
    *_window_sensors("7d", WINDOW_SHORT_DAYS, lambda data: data.short),
    *_window_sensors("30d", WINDOW_LONG_DAYS, lambda data: data.long),
]


async def async_setup_entry(
    hass: HomeAssistant, entry: RyotConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(RyotSensor(coordinator, d) for d in SENSORS)

    # Measurement statistics are user-defined in Ryot, so their sensors are
    # created as values for them first show up.
    known: set[str] = set()

    @callback
    def add_measurements() -> None:
        new = [n for n in coordinator.data.measurements if n not in known]
        known.update(new)
        if new:
            async_add_entities(RyotMeasurementSensor(coordinator, n) for n in new)

    add_measurements()
    entry.async_on_unload(coordinator.async_add_listener(add_measurements))


class RyotEntity(CoordinatorEntity[RyotCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: RyotCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id)},
            name=entry.title,
            manufacturer="Ryot",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=entry.data["url"],
        )


class RyotSensor(RyotEntity, SensorEntity):
    entity_description: RyotSensorDescription

    def __init__(self, coordinator: RyotCoordinator, description: RyotSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def native_unit_of_measurement(self) -> str | None:
        if fn := self.entity_description.unit_fn:
            return fn(self.coordinator.data)
        return self.entity_description.native_unit_of_measurement

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if fn := self.entity_description.attrs_fn:
            return fn(self.coordinator.data)
        return None


class RyotMeasurementSensor(RyotEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "measurement"

    def __init__(self, coordinator: RyotCoordinator, stat: str) -> None:
        super().__init__(coordinator, f"measurement_{stat}")
        self._stat = stat
        self._attr_translation_placeholders = {"name": stat.replace("_", " ").capitalize()}
        unit = coordinator.data.user.measurement_units.get(stat)
        if unit is None and stat == "weight":
            unit = _mass(coordinator.data)
            self._attr_device_class = SensorDeviceClass.WEIGHT
        elif unit is None and ("fat" in stat or "percent" in stat):
            unit = PERCENTAGE
        self._attr_native_unit_of_measurement = unit

    @property
    def available(self) -> bool:
        return super().available and self._stat in self.coordinator.data.measurements

    @property
    def native_value(self) -> float | None:
        m = self.coordinator.data.measurements.get(self._stat)
        return m.value if m else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        m = self.coordinator.data.measurements.get(self._stat)
        return {"measured_at": m.timestamp.isoformat()} if m else None
