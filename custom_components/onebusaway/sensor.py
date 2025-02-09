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
    coordinator = OneBusAwayCoordinator(hass, client, async_add_devices, stop_id)
    await coordinator.async_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator


class OneBusAwayCoordinator:
    """Manages dynamic sensor creation and updates."""

    def __init__(self, hass, client, async_add_devices, stop_id):
        self.hass = hass
        self.client = client
        self.async_add_devices = async_add_devices
        self.stop_id = stop_id
        self.sensors = {}
        self.polling_job = None

    async def async_refresh(self, _=None):
        """Fetch new data and update or create sensors."""
        data = await self.client.async_get_data()
        arrivals = self.compute_arrivals(time(), data)

        # Update or create sensors for each arrival
        new_sensors = []
        for index, arrival in enumerate(arrivals):
            unique_id = f"{self.stop_id}_arrival_{index}"
            if unique_id not in self.sensors:
                sensor = OneBusAwayArrivalSensor(self.stop_id, arrival, index)
                self.sensors[unique_id] = sensor
                new_sensors.append(sensor)
            else:
                # Update existing sensor
                self.sensors[unique_id].update_arrival(arrival)

        if new_sensors:
            self.async_add_devices(new_sensors)

        # Adjust polling interval
        self._schedule_next_poll(arrivals)

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

    def _schedule_next_poll(self, arrivals):
        """Schedule the next poll based on arrival times."""
        if self.polling_job:
            self.polling_job()  # Cancel previous job

        # Determine polling interval
        if arrivals:
            next_arrival_time = arrivals[0]["time"]
            seconds_until_next_arrival = next_arrival_time - time()

            if seconds_until_next_arrival <= 300:  # 5 minutes or less
                interval = timedelta(seconds=30)
            else:
                interval = timedelta(seconds=60)
        else:
            interval = timedelta(seconds=60)  # Default if no arrivals

        # Schedule the next poll
        self.polling_job = async_track_time_interval(self.hass, self.async_refresh, interval)

class OneBusAwayArrivalSensor(SensorEntity):
    """Sensor for an individual bus arrival."""

    def __init__(self, stop_id, arrival_info, index) -> None:
        """Initialize the sensor."""
        self.stop_id = stop_id
        self.index = index
        self._attr_unique_id = f"{stop_id}_arrival_{index}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, stop_id)},
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
        """Friendly name for the sensor."""
        if self.arrival_info:
            route = self.arrival_info["routeShortName"]
            headsign = self.arrival_info["headsign"]
            return f"{route} to {headsign}"
        return f"Bus Stop {self.stop_id} Arrival {self.index + 1}"

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

