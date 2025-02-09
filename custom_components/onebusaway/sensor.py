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
from homeassistant.const import CONF_URL, CONF_ID, CONF_TOKEN
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
    sensor = OneBusAwaySensor(client, entry.data[CONF_ID])
    async_add_devices([sensor])


class OneBusAwaySensor(SensorEntity):
    """OneBusAway Parent Sensor class."""

    def __init__(self, client: OneBusAwayApiClient, stop: str) -> None:
        """Initialize the parent sensor."""
        self.client = client
        self.stop = stop
        self._attr_unique_id = stop
        self._attr_name = "OneBusAway Sensor"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, stop)},
            name=NAME,
            model=VERSION,
            manufacturer=NAME,
        )
        self.child_sensors = {}
        self.data = None
        self.unsub = None

    async def async_update(self):
        """Retrieve the latest state."""
        self.data = await self.client.async_get_data()

        # Update child sensors
        self.update_child_sensors()

        # Schedule next state refresh
        if self.unsub:
            self.unsub()
        if self.data:
            arrivals = self.data.get("data", {}).get("entry", {}).get("arrivalsAndDepartures", [])
            if arrivals:
                next_arrival_time = arrivals[0].get("predictedArrivalTime") or arrivals[0].get("scheduledDepartureTime")
                if next_arrival_time:
                    self.unsub = async_track_point_in_time(
                        self.hass, self.refresh, datetime.fromtimestamp(next_arrival_time / 1000, timezone.utc)
                    )

    def refresh(self, _timestamp) -> None:
        """Invalidate the current sensor state."""
        self.schedule_update_ha_state(True)

    def update_child_sensors(self):
        """Update child sensors for each arrival."""
        arrivals = self.data.get("data", {}).get("entry", {}).get("arrivalsAndDepartures", [])
        
        for index, arrival in enumerate(arrivals):
            # Use index for consistent child entity naming
            if index not in self.child_sensors:
                child_sensor = OneBusAwayArrivalSensor(self.stop, index, arrival)
                self.child_sensors[index] = child_sensor
                self.hass.async_create_task(self.hass.helpers.entity_platform.async_add_entities([child_sensor]))
            else:
                self.child_sensors[index].update_arrival(arrival)


class OneBusAwayArrivalSensor(SensorEntity):
    """Represents a single bus trip as a child sensor."""

    def __init__(self, stop: str, index: int, arrival: dict) -> None:
        """Initialize the child sensor."""
        self._stop = stop
        self._index = index
        self._attr_unique_id = f"{stop}_{index}"
        self.update_arrival(arrival)

    def update_arrival(self, arrival: dict):
        """Update the sensor with new arrival data."""
        route_name = arrival.get("routeShortName", "Unknown Route")
        headsign = arrival.get("headsign", "Unknown Destination")
        arrival_time = arrival.get("predictedArrivalTime") or arrival.get("scheduledDepartureTime")
        if arrival_time:
            self._attr_native_value = datetime.fromtimestamp(arrival_time / 1000, timezone.utc)
        else:
            self._attr_native_value = None

        self._attr_name = f"Route {route_name} to {headsign}"
        self._attr_extra_state_attributes = {
            "route": f"{route_name} to {headsign}",
            "type": "Predicted" if "predictedArrivalTime" in arrival else "Scheduled",
        }

        # Schedule an update for Home Assistant
        if self.hass:
            self.async_write_ha_state()
