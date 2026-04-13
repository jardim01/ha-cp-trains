"""Sensor for CP Trains integration."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CONF_TRAIN_NUMBER
from .coordinator import CPTrainsCoordinator

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([CPTrainsSensor(coordinator, entry)])

class CPTrainsSensor(CoordinatorEntity[CPTrainsCoordinator], SensorEntity):
    """Representation of a CP Train sensor."""

    def __init__(self, coordinator: CPTrainsCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_name = f"CP Train {entry.data[CONF_TRAIN_NUMBER]}"
        self._attr_unique_id = f"cp_train_{entry.data[CONF_TRAIN_NUMBER]}"
        self._attr_icon = "mdi:train"

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        return self.coordinator.data.get("state")

    @property
    def extra_state_attributes(self) -> dict[str, any] | None:
        """Return the state attributes."""
        data = self.coordinator.data
        if not data:
            return None

        return {
            "train_number": data.get("train_number"),
            "service": data.get("service"),
            "origin": data.get("origin"),
            "destination": data.get("destination"),
            "scheduled_departure": data.get("scheduled_departure"),
            "scheduled_arrival": data.get("scheduled_arrival"),
            "status_text": data.get("status_text"),
            "delay": data.get("delay"),
            "stations": data.get("stations")
        }
