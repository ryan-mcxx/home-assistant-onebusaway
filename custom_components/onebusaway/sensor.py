import re
from datetime import datetime, timezone, timedelta
from time import time

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.const import CONF_URL, CONF_ID, CONF_TOKEN
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

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

    stop_id = entry.data[CONF_ID]
    coordinator = OneBusAwaySensorCoordinator(hass, client, async_add_devices, stop_id)
    await coordinator.async_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator


class OneBusAwaySensorCoordinator:
    """Manages and updates OneBusAway sensors."""

    def __init__(self, hass, client, async_add_entities, stop_id):
        """Initialize the coordinator."""
        self.hass = hass
        self.stop_id = stop_id
        self.client = client
        self.sensors = []
        self.async_add_entities = async_add_entities
        self.situations_sensor = None  # Declare situations sensor here
        self._unsub = None

    async def async_refresh(self):
        """Retrieve the latest state and update sensors."""
        await self.async_update()
        await self.schedule_updates()

    async def async_update(self):
        """Retrieve the latest state and update sensors."""
        self.data = await self.client.async_get_data()

        # Compute new arrival times
        new_arrival_times = self.compute_arrivals(time())

        # Ensure enough sensors are created for all arrivals
        if len(new_arrival_times) > len(self.sensors):
            for index in range(len(self.sensors), len(new_arrival_times)):
                new_sensor = OneBusAwayArrivalSensor(
                    stop_id=self.stop_id,
                    arrival_info=new_arrival_times[index],
                    index=index,
                )
                self.sensors.append(new_sensor)
                self.async_add_entities([new_sensor])

        # Update existing sensors
        for index, sensor in enumerate(self.sensors):
            if isinstance(sensor, OneBusAwayArrivalSensor):
                if index < len(new_arrival_times):
                    sensor.update_arrival(new_arrival_times[index])
                else:
                    sensor.clear_arrival()

        # Handle situations sensor update
        situations = self.data.get("data", {}).get("references", {}).get("situations", [])

        # Create situations sensor if it doesn't exist
        if not self.situations_sensor:
            self.situations_sensor = OneBusAwaySituationsSensor(self.stop_id)
            self.async_add_entities([self.situations_sensor])
            self.sensors.append(self.situations_sensor)

        # Update situations sensor with new data
        self.situations_sensor.update_situations(situations)

class OneBusAwayArrivalSensor(SensorEntity):
    """Sensor for an individual bus arrival."""

    def __init__(self, stop_id, arrival_info, index) -> None:
        """Initialize the sensor."""
        self.stop_id = stop_id
        self.index = index
        self._attr_unique_id = f"{stop_id}_arrival_{index}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, stop_id)},
            name=f"Stop {stop_id}",
            model=VERSION,
            manufacturer=NAME,
        )
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_attribution = ATTRIBUTION
        self.arrival_info = arrival_info
        # Explicitly set a custom entity ID
        self.entity_id = f"sensor.onebusaway_{stop_id}_arrival_{index}"

    def update_arrival(self, arrival_info):
        """Update the sensor with new arrival information."""
        self.arrival_info = arrival_info
        self.async_write_ha_state()

    def clear_arrival(self):
        """Clear the arrival state when no data is available."""
        self.arrival_info = None
        self.async_write_ha_state()

    @property
    def native_value(self) -> datetime | None:
        """Return the time for this specific bus arrival."""
        return datetime.fromtimestamp(self.arrival_info["time"], timezone.utc) if self.arrival_info else None

    @property
    def name(self) -> str:
        """Friendly name for the sensor."""
        if self.arrival_info:
            route = self.arrival_info["routeShortName"]
            headsign = self.arrival_info["headsign"]
            return f"{route} to {headsign}"
        return f"OneBusAway {self.stop_id} Arrival {self.index + 1}"

    @property
    def extra_state_attributes(self):
        """Return additional metadata for this bus arrival."""
        if not self.arrival_info:
            return {}
        return {
            "arrival time": self.arrival_info["type"],
            "route": self.arrival_info["routeShortName"],
        }

    @property
    def icon(self) -> str:
        """Return the icon for this sensor based on arrival type."""
        if self.arrival_info:
            if self.arrival_info["type"].lower() == "predicted":
                return "mdi:rss"
            else:
                return "mdi:timeline-clock-outline"
        return "mdi:bus"

class OneBusAwaySituationsSensor(SensorEntity):
    """Sensor for tracking situations affecting the bus stop."""

    def __init__(self, stop_id: str) -> None:
        """Initialize the sensor."""
        self.stop_id = stop_id
        self._attr_unique_id = f"{stop_id}_situations"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, stop_id)},
            name=f"Situations for Stop {stop_id}",
            model=VERSION,
            manufacturer=NAME,
        )
        self._situations = []

        # Explicitly set a custom entity ID
        self.entity_id = f"sensor.onebusaway_{stop_id}_situations"

    def update_situations(self, situations: list[dict]) -> None:
        """Update the state and attributes based on new situation data."""
        self._situations = situations
        self.async_write_ha_state()

    @property
    def native_value(self) -> int:
        """Return the number of active situations."""
        return len(self._situations)

    @property
    def extra_state_attributes(self) -> dict:
        """Return situation details as attributes."""
        attributes = {}
        for index, situation in enumerate(self._situations):
            reason = situation.get("reason", "Unknown Reason")
            summary = situation.get("summary", {}).get("value", "No Summary")
            attributes[f"situation_{index + 1}"] = f"{reason} - {summary}"
        return attributes
