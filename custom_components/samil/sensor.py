"""Sensors for the Samil Power integration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_MODEL, DOMAIN
from .coordinator import SamilConfigEntry, SamilCoordinator

OFFLINE_STATE = "Offline"


class Offline(Enum):
    """What a sensor shows while the inverter is off (e.g. at night)."""

    ZERO = "zero"  # power/current: 0
    KEEP = "keep"  # energy counters: last known value
    UNAVAILABLE = "unavailable"  # voltages, temperatures, ...


@dataclass(frozen=True, kw_only=True)
class SamilSensorDescription(SensorEntityDescription):
    offline: Offline = Offline.UNAVAILABLE


def _power(key: str, name: str) -> SamilSensorDescription:
    return SamilSensorDescription(
        key=key, name=name, native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER, state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0, offline=Offline.ZERO,
    )


def _voltage(key: str, name: str) -> SamilSensorDescription:
    return SamilSensorDescription(
        key=key, name=name, native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    )


def _current(key: str, name: str) -> SamilSensorDescription:
    return SamilSensorDescription(
        key=key, name=name, native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT, state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1, offline=Offline.ZERO,
    )


def _frequency(key: str, name: str) -> SamilSensorDescription:
    return SamilSensorDescription(
        key=key, name=name, native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY, state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    )


def _temperature(key: str, name: str) -> SamilSensorDescription:
    return SamilSensorDescription(
        key=key, name=name, native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE, state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    )


COMMON_SENSORS: tuple[SamilSensorDescription, ...] = (
    SamilSensorDescription(
        key="operation_mode", name="Operation mode", device_class=SensorDeviceClass.ENUM,
        options=["Wait", "Normal", "Fault", "Permanent fault", "Check", "PV power off", OFFLINE_STATE],
        icon="mdi:solar-power-variant",
    ),
    _power("output_power", "Output power"),
    _power("pv1_input_power", "PV1 input power"),
    _power("pv2_input_power", "PV2 input power"),
    _voltage("pv1_voltage", "PV1 voltage"),
    _voltage("pv2_voltage", "PV2 voltage"),
    _current("pv1_current", "PV1 current"),
    _current("pv2_current", "PV2 current"),
    SamilSensorDescription(
        key="energy_today", name="Energy today", native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY, state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2, offline=Offline.KEEP,
    ),
    SamilSensorDescription(
        key="energy_total", name="Energy total", native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY, state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1, offline=Offline.KEEP,
    ),
    _temperature("internal_temperature", "Internal temperature"),
    _temperature("heatsink_temperature", "Heatsink temperature"),
    SamilSensorDescription(
        key="total_operation_time", name="Total operation time",
        native_unit_of_measurement=UnitOfTime.HOURS, device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING, entity_category=EntityCategory.DIAGNOSTIC,
        offline=Offline.KEEP,
    ),
)

SINGLE_PHASE_SENSORS = (
    _voltage("grid_voltage", "Grid voltage"),
    _current("grid_current", "Grid current"),
    _frequency("grid_frequency", "Grid frequency"),
)

THREE_PHASE_SENSORS = tuple(
    desc
    for phase in ("r", "s", "t")
    for desc in (
        _voltage(f"grid_voltage_{phase}_phase", f"Grid voltage {phase.upper()}"),
        _current(f"grid_current_{phase}_phase", f"Grid current {phase.upper()}"),
        _frequency(f"grid_frequency_{phase}_phase", f"Grid frequency {phase.upper()}"),
    )
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SamilConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    model = entry.data.get(DATA_MODEL, {})
    three_phase = "Three-phase" in model.get("device_type", "") or any(
        k.endswith("_r_phase") for k in (coordinator.data.status if coordinator.data else {})
    )
    descriptions = COMMON_SENSORS + (THREE_PHASE_SENSORS if three_phase else SINGLE_PHASE_SENSORS)
    async_add_entities(SamilSensor(coordinator, entry, d) for d in descriptions)


class SamilSensor(CoordinatorEntity[SamilCoordinator], SensorEntity):
    """A single inverter value."""

    _attr_has_entity_name = True
    entity_description: SamilSensorDescription

    def __init__(
        self, coordinator: SamilCoordinator, entry: SamilConfigEntry, description: SamilSensorDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        serial = entry.unique_id or entry.entry_id
        self._attr_unique_id = f"{serial}_{description.key}"
        model = entry.data.get(DATA_MODEL, {})
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            manufacturer=model.get("manufacturer") or "Samil Power",
            model=model.get("model_name") or None,
            serial_number=model.get("serial_number") or None,
            sw_version=model.get("firmware_version") or None,
            name=f"Samil {model.get('model_name') or 'inverter'}".strip(),
        )

    @property
    def available(self) -> bool:
        data = self.coordinator.data
        if data is None:
            return False
        key = self.entity_description.key
        if data.online:
            return key in data.status
        if key == "operation_mode":
            return True
        match self.entity_description.offline:
            case Offline.ZERO:
                return True
            case Offline.KEEP:
                return key in data.status
            case _:
                return False

    @property
    def native_value(self):
        data = self.coordinator.data
        key = self.entity_description.key
        if data is None:
            return None
        if not data.online:
            if key == "operation_mode":
                return OFFLINE_STATE
            if self.entity_description.offline is Offline.ZERO:
                return 0
        value = data.status.get(key)
        if self.entity_description.options and value not in self.entity_description.options:
            return None  # unknown operation mode code
        return value
