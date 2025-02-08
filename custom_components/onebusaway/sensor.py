"""Sensor platform for onebusaway."""
from __future__ import annotations
from datetime import datetime, timezone
from time import time

from homeassistant.helpers.entity import DeviceInfo

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorDeviceClass,
)
from homeassistant.const import (
    CONF_URL,
    CONF_ID,
    CONF_TOKEN,
)
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import ATTRIBUTION, DOMAIN, NAME, VERSION

from .api import OneBusAwayApiClient

ENTITY_DESCRIPTIONS = (
    SensorEntityDescription(
        key="onebusaway",
        name="OneBusAway Sensor",
        icon="mdi:bus-clock",
    ),
)


async def async_setup_entry(hass, entry, async_add_devices):
    """Set up the sensor platform."""
    client = OneBusAwayApiClient(
        url=entry.data[CONF_URL],
        key=entry.data[CONF_TOKEN],
        stop=entry.data[CONF_ID],
        session=async_get_clientsession(hass),
    )
    async_add_devices(
        OneBusAwaySensor(
            client=client,
            entity_description=entity_description,
            stop=entry.data[CONF_ID],
        )
        for entity_description in ENTITY_DESCRIPTIONS
    )


class OneBusAwaySensor(SensorEntity):
    """OneBusAway Sensor class."""

    def __init__(self, client: OneBusAwayApiClient, entity_description: SensorEntityDescription, stop: str) -> None:
        """Initialize the sensor class."""
        super().__init__()
        self.entity_description = entity_description
        self.client = client
        self._attr_attribution = ATTRIBUTION
        self._attr_unique_id = stop
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, stop)},
            name=NAME,
            model=VERSION,
            manufacturer=NAME,
        )

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    data = None
    unsub = None
    arrival_times = []  # Store all arrival times with metadata

    def compute_arrivals(self, after) -> list[dict]:
        """Compute all upcoming arrival times after the given timestamp."""
        if self.data is None:
            return []

        current = after * 1000

        def extract_departure(d) -> dict | None:
            """Extract time and type (predicted or scheduled)."""
            predicted = d.get("predictedArrivalTime")
            scheduled = d.get("scheduledDepartureTime")
            if predicted and predicted > current:
                return {"time": predicted / 1000, "type": "Predicted"}
            elif scheduled and scheduled > current:
                return {"time": scheduled / 1000, "type": "Scheduled"}
            return None

        # Collect valid departure data
        departures = [
            dep for d in self.data.get("data", {}).get("entry", {}).get("arrivalsAndDepartures", [])
            if (dep := extract_departure(d)) is not None
        ]

        # Sort by time and return
        return sorted(departures, key=lambda x: x["time"])

    def refresh(self, _timestamp) -> None:
        """Invalidate the current sensor state."""
        self.schedule_update_ha_state(True)

    @property
    def native_value(self) -> str | None:
        """Return the next bus arrival time."""
        return datetime.fromtimestamp(self.arrival_times[0]["time"], timezone.utc).isoformat() if self.arrival_times else None

    @property
    def extra_state_attributes(self):
        """Return attributes for each bus arrival."""
        attrs = {}
        for index, arrival in enumerate(self.arrival_times, start=1):
            arrival_time = datetime.fromtimestamp(arrival["time"], timezone.utc).isoformat()
            attrs[f"Arrival {index} Time"] = arrival_time
            attrs[f"Arrival {index} Type"] = arrival["type"]
        return attrs

    async def async_update(self):
        """Retrieve the latest state."""
        self.data = await self.client.async_get_data()

        # Update arrival times
        self.arrival_times = self.compute_arrivals(time())

        if self.arrival_times:
            if self.unsub:
                self.unsub()
            # Set a timer for the next arrival to refresh the state
            self.unsub = async_track_point_in_time(
                self.hass, self.refresh, datetime.fromtimestamp(self.arrival_times[0]["time"], timezone.utc)
            )
