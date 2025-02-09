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
            if index < len(new_arrival_times):
                # Update existing sensor with arrival data
                sensor.update_arrival(new_arrival_times[index])
            else:
                # No corresponding arrival, set state to None
                sensor.clear_arrival()

    def compute_arrivals(self, after) -> list[dict]:
        """Compute all upcoming arrival times after the given timestamp."""
        if self.data is None:
            return []

        current = after * 1000

        def extract_departure(d) -> dict | None:
            """Extract time, type, route name, and trip headsign."""
            predicted = d.get("predictedArrivalTime")
            scheduled = d.get("scheduledDepartureTime")
            trip_headsign = d.get("tripHeadsign", "Unknown")
            route_name = d.get("routeShortName", "Unknown Route")

            if predicted and predicted > current:
                return {"time": predicted / 1000, "type": "Predicted", "headsign": trip_headsign, "routeShortName": route_name}
            elif scheduled and scheduled > current:
                return {"time": scheduled / 1000, "type": "Scheduled", "headsign": trip_headsign, "routeShortName": route_name}
            return None

        # Collect valid departures
        departures = [
            dep for d in self.data.get("data", {}).get("entry", {}).get("arrivalsAndDepartures", [])
            if (dep := extract_departure(d)) is not None
        ]

        # Sort by time
        return sorted(departures, key=lambda x: x["time"])

    def next_arrival_within_5_minutes(self) -> bool:
        """Check if the next arrival is within 5 minutes."""
        if self.data:
            arrivals = self.compute_arrivals(time())
            if arrivals:
                next_arrival = arrivals[0]["time"]
                return next_arrival <= (time() + 5 * 60)
        return False

    async def schedule_updates(self):
        """Schedule sensor updates dynamically."""
        async def update_interval(_):
            await self.async_update()
            await self.schedule_updates()

        next_interval = timedelta(seconds=30 if self.next_arrival_within_5_minutes() else 60)
        if self._unsub:
            self._unsub()
        self._unsub = async_track_time_interval(self.hass, update_interval, next_interval)


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
