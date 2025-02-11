import re
from datetime import datetime, timezone, timedelta
from time import time
import logging

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.const import CONF_URL, CONF_ID, CONF_TOKEN
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .const import ATTRIBUTION, DOMAIN, NAME, VERSION
from .api import OneBusAwayApiClient

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_devices):
    """Set up the sensor platform with multiple stops."""
    stops = entry.data.get("stops", [entry.data[CONF_ID]])  # Support multiple stops
    session = async_get_clientsession(hass)

    coordinators = []
    for stop_id in stops:
        client = OneBusAwayApiClient(
            url=entry.data[CONF_URL],
            key=entry.data[CONF_TOKEN],
            stop=stop_id,
            session=session,
        )
        coordinator = OneBusAwaySensorCoordinator(hass, client, async_add_devices, stop_id)
        await coordinator.async_refresh()
        coordinators.append(coordinator)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinators

class OneBusAwaySensorCoordinator:
    """Manages and updates OneBusAway sensors."""

    def __init__(self, hass, client, async_add_entities, stop_id):
        self.hass = hass
        self.stop_id = stop_id
        self.client = client
        self.sensors = []
        self.async_add_entities = async_add_entities
        self._unsub = None

    async def async_refresh(self):
        _LOGGER.info("Refreshing data for stop %s", self.stop_id)
        await self.async_update()
        await self.schedule_updates()

    async def async_update(self):
        try:
            self.data = await self.client.async_get_data(self.stop_id)
            _LOGGER.debug("Data for stop %s: %s", self.stop_id, self.data)
        except Exception as e:
            _LOGGER.error("Error fetching data for stop %s: %s", self.stop_id, e)
            return

        new_arrival_times = self.compute_arrivals(time())
        situation_data = self.data.get("data", {}).get("references", {}).get("situations", [])

        # Handle situation sensors
        situation_sensor = next((s for s in self.sensors if isinstance(s, OneBusAwaySituationSensor)), None)
        if not situation_sensor:
            situation_sensor = OneBusAwaySituationSensor(self.stop_id, situation_data)
            self.sensors.append(situation_sensor)
            self.async_add_entities([situation_sensor])
        else:
            situation_sensor.update_situations(situation_data)

        # Ensure enough sensors for all arrivals
        for index, arrival_info in enumerate(new_arrival_times):
            if index >= len(self.sensors) or not isinstance(self.sensors[index], OneBusAwayArrivalSensor):
                new_sensor = OneBusAwayArrivalSensor(self.stop_id, arrival_info, index)
                self.sensors.append(new_sensor)
                self.async_add_entities([new_sensor])
            else:
                self.sensors[index].update_arrival(arrival_info)

        # Clear extra sensors if fewer arrivals
        for extra_sensor in self.sensors[len(new_arrival_times):]:
            if isinstance(extra_sensor, OneBusAwayArrivalSensor):
                extra_sensor.clear_arrival()

    def compute_arrivals(self, after) -> list[dict]:
        """Compute all upcoming arrival times after the given timestamp."""
        if self.data is None:
            return []

        current = after * 1000
        arrivals = []

        for d in self.data.get("data", {}).get("entry", {}).get("arrivalsAndDepartures", []):
            predicted = d.get("predictedArrivalTime")
            scheduled = d.get("scheduledDepartureTime")
            trip_headsign = d.get("tripHeadsign", "Unknown")
            route_name = d.get("routeShortName", "Unknown Route")

            time_info = None
            if predicted and predicted > current:
                time_info = {"time": predicted / 1000, "type": "Predicted"}
            elif scheduled and scheduled > current:
                time_info = {"time": scheduled / 1000, "type": "Scheduled"}

            if time_info:
                arrivals.append({**time_info, "headsign": trip_headsign, "routeShortName": route_name})

        return sorted(arrivals, key=lambda x: x["time"])

    async def schedule_updates(self):
        """Schedule sensor updates dynamically."""
        async def update_interval(_):
            _LOGGER.info("Updating stop %s", self.stop_id)
            await self.async_update()
            await self.schedule_updates()

        next_interval_seconds = 60 if self.compute_arrivals(time()) else 300
        _LOGGER.info("Next update for stop %s in %d seconds", self.stop_id, next_interval_seconds)

        next_interval = timedelta(seconds=next_interval_seconds)
        if self._unsub:
            self._unsub()
        self._unsub = async_track_time_interval(self.hass, update_interval, next_interval)

class OneBusAwayArrivalSensor(SensorEntity):
    """Sensor for an individual bus arrival."""

    def __init__(self, stop_id, arrival_info, index) -> None:
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
        self.entity_id = f"sensor.onebusaway_{stop_id}_arrival_{index}"

    def update_arrival(self, arrival_info):
        self.arrival_info = arrival_info
        self.async_write_ha_state()

    def clear_arrival(self):
        self.arrival_info = None
        self.async_write_ha_state()

    @property
    def native_value(self) -> datetime | None:
        return datetime.fromtimestamp(self.arrival_info["time"], timezone.utc) if self.arrival_info else None

    @property
    def name(self) -> str:
        if self.arrival_info:
            route = self.arrival_info["routeShortName"]
            headsign = self.arrival_info["headsign"]
            return f"{route} to {headsign}"
        return f"OneBusAway {self.stop_id} Arrival {self.index}"

    @property
    def extra_state_attributes(self):
        if not self.arrival_info:
            return {}
        return {
            "arrival_time": self.arrival_info["type"],
            "route_name": self.arrival_info["routeShortName"],
        }

    @property
    def icon(self) -> str:
        if self.arrival_info:
            return "mdi:rss" if self.arrival_info["type"].lower() == "predicted" else "mdi:timetable"
        return "mdi:bus"

class OneBusAwaySituationSensor(SensorEntity):
    """Sensor to display the count of situations and their details."""

    def __init__(self, stop_id, situations) -> None:
        self.stop_id = stop_id
        self._attr_unique_id = f"{stop_id}_situation_count"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, stop_id)},
            name=f"Stop {stop_id}",
            model=VERSION,
            manufacturer=NAME,
        )
        self._attr_attribution = ATTRIBUTION
        self.situations = situations
        self.entity_id = f"sensor.onebusaway_{stop_id}_situations"

    def update_situations(self, situations: list[dict]):
        self.situations = situations
        self.async_write_ha_state()

    @property
    def native_value(self) -> int:
        return len(self.situations)

    @property
    def name(self) -> str:
        return f"Situations at Stop {self.stop_id}"

    @property
    def icon(self) -> str:
        return "mdi:alert-circle-check"

    def _sanitize_text(self, text: str) -> str:
        return re.sub(r"[\r\n]+", " ", text).strip()

    @property
    def extra_state_attributes(self):
        attributes = {}
        markdown_lines = []

        for index, situation in enumerate(self.situations):
            severity = situation.get("severity", "Unknown")
            reason = self._sanitize_text(situation.get("reason", "Not specified"))
            summary = self._sanitize_text(situation.get("summary", {}).get("value", "")).strip()
            url = situation.get("url", {}).get("value", "")

            if summary and url:
                if index > 0:
                    markdown_lines.append("\n---\n")
                markdown_lines.append(
                    f"**Severity:** {severity}  \n"
                    f"**Reason:** {reason}  \n"
                    f"[{summary}]({url})"
                )

        attributes["markdown_content"] = "\n".join(markdown_lines)
        return attributes
