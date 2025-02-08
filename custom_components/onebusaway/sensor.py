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

    # Register the function to add child sensors dynamically
    parent_sensor.register_child_sensors = async_add_devices


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
        self.child_sensors = {}

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

        if self.arrival_times:
            if self.unsub:
                self.unsub()
            # Set a timer for the next arrival to refresh the state
            self.unsub = async_track_point_in_time(
                self.hass, self.refresh, datetime.fromtimestamp(self.arrival_times[0]["time"], timezone.utc)
            )

        # Update or create child sensors
        self.update_child_sensors()

    def refresh(self, _timestamp) -> None:
        """Invalidate the current sensor state."""
        self.schedule_update_ha_state(True)

    def update_child_sensors(self):
        """Update or create child sensors."""
        if not self.register_child_sensors:
            return

        new_sensors = []
        existing_ids = set(self.child_sensors.keys())

        for arrival in self.arrival_times:
            sensor_id = f"{self.stop}_{int(arrival['time'])}"
            if sensor_id not in existing_ids:
                # Create a new sensor and track it
                child_sensor = OneBusAwayArrivalSensor(self.stop, arrival)
                self.child_sensors[sensor_id] = child_sensor
                new_sensors.append(child_sensor)
            else:
                # Update existing sensor
                self.child_sensors[sensor_id].update_arrival(arrival)

        if new_sensors:
            self.register_child_sensors(new_sensors)


class OneBusAwayArrivalSensor(SensorEntity):
    """Represents a single bus arrival sensor."""

    def __init__(self, stop: str, arrival: dict) -> None:
        """Initialize the sensor for a specific bus arrival."""
        self._attr_unique_id = f"{stop}_{int(arrival['time'])}"
        self.update_arrival(arrival)

    def update_arrival(self, arrival: dict):
        """Update the sensor with new arrival data."""
        self._attr_name = f"{arrival['routeShortName']} to {arrival['headsign']}"
        self._attr_native_value = datetime.fromtimestamp(arrival["time"], timezone.utc)
        self._attr_extra_state_attributes = {
            "Type": arrival["type"]
        }
        self.async_write_ha_state()
