"""Sensor platform for OneBusAway."""
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
    
    parent_sensor = OneBusAwaySensor(
        client=client,
        entity_description=ENTITY_DESCRIPTIONS[0],
        stop=entry.data[CONF_ID],
    )

    # Add parent sensor first
    async_add_devices([parent_sensor])

    # Create arrival sensors dynamically on updates
    parent_sensor.register_child_sensors = lambda sensors: async_add_devices(sensors)


class OneBusAwaySensor(SensorEntity):
    """OneBusAway Parent Sensor class."""

    def __init__(
        self,
        client: OneBusAwayApiClient,
        entity_description: SensorEntityDescription,
        stop: str,
    ) -> None:
        """Initialize the parent sensor."""
        super().__init__()
        self.entity_description = entity_description
        self.client = client
        self.stop = stop
        self.arrival_times = []
        self.data = None
        self.unsub = None
        self.register_child_sensors = None

    def compute_arrivals(self, after) -> list[dict]:
        """Compute all upcoming arrival times after the given timestamp."""
        if self.data is None:
            return []

        current = after * 1000

        def extract_departure(d) -> dict | None:
            """Extract time, type, and trip headsign."""
            predicted = d.get("predictedArrivalTime")
            scheduled = d.get("scheduledDepartureTime")
            trip_headsign = d.get("tripHeadsign", "Unknown")
            route_name = d.get("routeShortName", "Unknown Route")
            if predicted and predicted > current:
                return {"time": predicted / 1000, "type": "Predicted", "headsign": trip_headsign, "routeShortName": route_name}
            elif scheduled and scheduled > current:
                return {"time": scheduled / 1000, "type": "Scheduled", "headsign": trip_headsign, "routeShortName": route_name}
            return None

        # Collect valid departure data
        departures = [
            dep for d in self.data.get("data", {}).get("entry", {}).get("arrivalsAndDepartures", [])
            if (dep := extract_departure(d)) is not None
        ]

        # Sort by time and return
        return sorted(departures, key=lambda x: x["time"])

    async def async_update(self):
        """Retrieve the latest state and update child sensors."""
        self.data = await self.client.async_get_data()
        self.arrival_times = self.compute_arrivals(time())

        # Create child sensors dynamically
        if self.register_child_sensors and self.arrival_times:
            child_sensors = [
                OneBusAwayArrivalSensor(self.stop, arrival)
                for arrival in self.arrival_times
            ]
            self.register_child_sensors(child_sensors)

        if self.arrival_times:
            if self.unsub:
                self.unsub()
            # Set a timer for the next arrival to refresh the state
            self.unsub = async_track_point_in_time(
                self.hass, self.refresh, datetime.fromtimestamp(self.arrival_times[0]["time"], timezone.utc)
            )

    def refresh(self, _timestamp) -> None:
        """Invalidate the current sensor state."""
        self.schedule_update_ha_state(True)


class OneBusAwayArrivalSensor(SensorEntity):
    """Represents a single bus arrival sensor."""

    def __init__(self, stop: str, arrival: dict) -> None:
        """Initialize the sensor for a specific bus arrival."""
        self._attr_unique_id = f"{stop}_{int(arrival['time'])}"
        self._attr_name = f"{arrival['routeShortName']} to {arrival['headsign']}"
        self._attr_native_value = datetime.fromtimestamp(arrival["time"], timezone.utc)
        self._attr_extra_state_attributes = {
            "Type": arrival["type"]
        }
