"""Sensor platform for OneBusAway."""
from __future__ import annotations
from datetime import datetime, timezone
from time import time

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.const import CONF_URL, CONF_ID, CONF_TOKEN
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import ATTRIBUTION, DOMAIN, NAME, VERSION
from .api import OneBusAwayApiClient


async def async_setup_entry(hass, entry, async_add_devices):
    """Set up the sensor platform."""
    client = OneBusAwayApiClient(
        url=entry.data[CONF_URL],
        key=entry.data[CONF_TOKEN],
        stop=entry.data[CONF_ID],
        session=async_get_clientsession(hass),
    )

    coordinator = OneBusAwayCoordinator(hass, client, async_add_devices)
    await coordinator.async_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator


class OneBusAwayCoordinator:
    """Manages dynamic sensor creation and updates."""

    def __init__(self, hass, client, async_add_devices):
        self.hass = hass
        self.client = client
        self.async_add_devices = async_add_devices
        self.sensors = {}

    async def async_refresh(self):
        """Fetch new data and update or create sensors."""
        data = await self.client.async_get_data()
        arrivals = self.compute_arrivals(time(), data)

        # Update or create sensors for each arrival
        new_sensors = []
        for index, arrival in enumerate(arrivals):
            unique_id = f"{self.client.stop}_arrival_{index}"
            if unique_id not in self.sensors:
                sensor = OneBusAwayArrivalSensor(self.client, arrival, index)
                self.sensors[unique_id] = sensor
                new_sensors.append(sensor)
            else:
                # Update existing sensor
                self.sensors[unique_id].update_arrival(arrival)

        if new_sensors:
            self.async_add_devices(new_sensors)

    def compute_arrivals(self, after, data) -> list[dict]:
        """Compute all upcoming arrivals."""
        current = after * 1000

        def extract_departure(d):
            predicted = d.get("predictedArrivalTime")
            scheduled = d.get("scheduledDepartureTime")
            trip_headsign = d.get("tripHeadsign", "Unknown")
            route_name = d.get("routeShortName", "Unknown Route")

            if predicted and predicted > current:
                return {"time": predicted / 1000, "type": "Predicted", "headsign": trip_headsign, "routeShortName": route_name}
            elif scheduled and scheduled > current:
                return {"time": scheduled / 1000, "type": "Scheduled", "headsign": trip_headsign, "routeShortName": route_name}
            return None

        departures = [
            dep for d in data.get("data", {}).get("entry", {}).get("arrivalsAndDepartures", [])
            if (dep := extract_departure(d)) is not None
        ]

        return sorted(departures, key=lambda x: x["time"])


class OneBusAwayArrivalSensor(SensorEntity):
    """Sensor for an individual bus arrival."""

    def __init__(self, client, arrival_info, index) -> None:
        """Initialize the sensor."""
        self.client = client
        self.index = index
        self._attr_unique_id = f"{client.stop}_arrival_{index}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, client.stop)},
            name=NAME,
            model=VERSION,
            manufacturer=NAME,
        )
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_attribution = ATTRIBUTION
        self.arrival_info = arrival_info

    def update_arrival(self, arrival_info):
        """Update the sensor with new arrival information."""
        self.arrival_info = arrival_info
        self.async_write_ha_state()

    @property
    def native_value(self) -> datetime | None:
        """Return the time for this specific bus arrival."""
        return datetime.fromtimestamp(self.arrival_info["time"], timezone.utc) if self.arrival_info else None

    @property
    def name(self) -> str:
        """Dynamically set the sensor name."""
        if self.arrival_info:
            arrival = self.arrival_info
            return f"{arrival['routeShortName']} to {arrival['headsign']} (Arrival {self.index + 1})"
        return f"OneBusAway Arrival {self.index + 1}"

    @property
    def extra_state_attributes(self):
        """Return additional metadata for this bus arrival."""
        if not self.arrival_info:
            return {}
        return {
            "type": self.arrival_info["type"],
            "headsign": self.arrival_info["headsign"],
            "route": self.arrival_info["routeShortName"],
        }
