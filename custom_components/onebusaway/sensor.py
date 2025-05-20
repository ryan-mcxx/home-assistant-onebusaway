import re
from datetime import datetime, timezone, timedelta
from time import time

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.const import CONF_URL, CONF_ID, CONF_TOKEN
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
import logging

from .const import ATTRIBUTION, DOMAIN, NAME, VERSION
from .api import OneBusAwayApiClient

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_devices):
    """Set up the sensor platform with multiple stops."""
    stops = entry.data.get("stops", [entry.data[CONF_ID]])  # Support multiple stops
    selected_routes = entry.data.get("selected_routes", [])
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {"selected_routes": selected_routes}
    session = async_get_clientsession(hass)

    coordinators = []
    for stop_id in stops:
        client = OneBusAwayApiClient(
            url=entry.data[CONF_URL],
            key=entry.data[CONF_TOKEN],
            stop=stop_id,
            session=session,
        )
        coordinator = OneBusAwaySensorCoordinator(hass, client, async_add_devices, stop_id, entry.entry_id)
        await coordinator.async_refresh()
        coordinators.append(coordinator)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "selected_routes": selected_routes,
        "coordinators": coordinators
    }


class OneBusAwaySensorCoordinator:
    """Manages and updates OneBusAway sensors."""

    def __init__(self, hass, client, async_add_entities, stop_id, entry_id):
        """Initialize the coordinator."""
        self.hass = hass
        self.stop_id = stop_id
        self.client = client
        self.sensors = []
        self.async_add_entities = async_add_entities
        self._unsub = None
        self.entry_id = entry_id
        self.refresh_sensor = OneBusAwayRefreshSensor(stop_id)
        self.sensors.append(self.refresh_sensor)
        self.async_add_entities([self.refresh_sensor])
        self.backoff_index = 0
        self.backoff_repeats = 0
        
    async def async_refresh(self):
        """Retrieve the latest state and update sensors."""
        _LOGGER.debug("Refreshing data for stop %s", self.stop_id)
        await self.async_update()
        await self.schedule_updates()

    async def async_update(self):
        """Retrieve the latest state and update sensors."""
        try:
            self.data = await self.client.async_get_data(self.stop_id)
            _LOGGER.debug("Data for stop %s: %s", self.stop_id, self.data)
        except Exception as e:
            _LOGGER.error("Error fetching data for stop %s: %s", self.stop_id, e)
            return
    
        # Compute new arrival times
        new_arrival_times = self.compute_arrivals(time())
    
        # Update or create situation count sensor
        situation_data = []
        if self.data and isinstance(self.data, dict):
            situation_data = self.data.get("data", {}).get("references", {}).get("situations", [])

        if not any(isinstance(sensor, OneBusAwaySituationSensor) for sensor in self.sensors):
            situation_sensor = OneBusAwaySituationSensor(
                stop_id=self.stop_id,
                situations=situation_data,
            )
            self.sensors.append(situation_sensor)
            self.async_add_entities([situation_sensor])
        else:
            for sensor in self.sensors:
                if isinstance(sensor, OneBusAwaySituationSensor):
                    sensor.update_situations(situation_data)

        # Ensure sensors match the number of arrivals
        existing_arrival_sensors = [s for s in self.sensors if isinstance(s, OneBusAwayArrivalSensor)]
        
        # Create new sensors if needed
        for index, arrival_info in enumerate(new_arrival_times):
            if index < len(existing_arrival_sensors):
                existing_arrival_sensors[index].update_arrival(arrival_info)
            else:
                new_sensor = OneBusAwayArrivalSensor(
                    stop_id=self.stop_id,
                    arrival_info=arrival_info,
                    index=index,
                )
                self.sensors.append(new_sensor)
                self.async_add_entities([new_sensor])

        # Clear any extra sensors
        for index in range(len(new_arrival_times), len(existing_arrival_sensors)):
            existing_arrival_sensors[index].clear_arrival()

    def compute_arrivals(self, after) -> list[dict]:
        """Compute all upcoming arrival times after the given timestamp."""
        if self.data is None:
            return []

        selected_routes = self.hass.data.get(DOMAIN, {}).get(self.entry_id, {}).get("selected_routes", [])
        current = after * 1000

        def extract_departure(d) -> dict | None:
            """Extract time, type, route name, and trip headsign."""
            predicted = d.get("predictedArrivalTime")
            scheduled = d.get("scheduledDepartureTime")
            trip_headsign = d.get("tripHeadsign", "Unknown")
            route_name = d.get("routeShortName", "Unknown Route")
            route_id = d.get("routeId")

            # Filter out routes not in the selected list
            if selected_routes and route_id not in selected_routes:
                return None

            if predicted and predicted > current:
                return {"time": predicted / 1000, "type": "Predicted", "headsign": trip_headsign, "routeShortName": route_name}
            elif scheduled and scheduled > current:
                return {"time": scheduled / 1000, "type": "Scheduled", "headsign": trip_headsign, "routeShortName": route_name}
            return None

        # Collect valid departures
        departures = [
            extract_departure(d)
            for d in self.data.get("data", {}).get("entry", {}).get("arrivalsAndDepartures", [])
            if extract_departure(d) is not None
        ]

        # Sort by time
        return sorted(departures, key=lambda x: x["time"])

    def next_arrival_within_10_minutes(self) -> bool:
        """Check if the next arrival is within 10 minutes."""
        if self.data:
            arrivals = self.compute_arrivals(time())
            if arrivals:
                next_arrival = arrivals[0]["time"]
                return next_arrival <= (time() + 10 * 60)
        return False

    async def schedule_updates(self):
        """Schedule sensor updates with intelligent stepwise backoff logic."""
        async def update_interval(_):
            _LOGGER.debug("Updating stop %s", self.stop_id)
            await self.async_update()
            await self.schedule_updates()
    
        arrivals = self.compute_arrivals(time())
        seconds_until_arrival = arrivals[0]["time"] - time() if arrivals else float("inf")
    
        # Define polling tiers
        polling_tiers = [30, 60, 90, 180, 300]
        max_interval = polling_tiers[-1]
    
        # Determine desired interval based on time until arrival
        if seconds_until_arrival <= 3 * 60:
            desired_interval = 30
        elif seconds_until_arrival <= 6 * 60:
            desired_interval = 60
        elif seconds_until_arrival <= 10 * 60:
            desired_interval = 90
        elif seconds_until_arrival <= 15 * 60:
            desired_interval = 180
        else:
            desired_interval = 300
    
        # Get or initialize current tier index and repeat counter
        current_interval = getattr(self, "_current_interval", max_interval)
        tier_index = polling_tiers.index(current_interval) if current_interval in polling_tiers else len(polling_tiers) - 1
        desired_index = polling_tiers.index(desired_interval)
    
        self._repeat_count = getattr(self, "_repeat_count", 0)
    
        if desired_index < tier_index:
            # Bus got closer — jump immediately to tighter polling
            tier_index = desired_index
            self._repeat_count = 0
        elif desired_index > tier_index:
            # Back off gradually, repeating once at current level
            if self._repeat_count == 0:
                self._repeat_count += 1  # repeat once
            else:
                tier_index = min(tier_index + 1, len(polling_tiers) - 1)
                self._repeat_count = 0
        # else: stay at current level
    
        next_interval_seconds = polling_tiers[tier_index]
        self._current_interval = next_interval_seconds
    
        # Schedule next update
        next_update_time = datetime.now(timezone.utc) + timedelta(seconds=next_interval_seconds)
        self.refresh_sensor.update_refresh_time(next_update_time)
    
        _LOGGER.debug(
            "Next update for stop %s in %d seconds (next arrival in %.1f min)",
            self.stop_id,
            next_interval_seconds,
            seconds_until_arrival / 60,
        )
    
        if self._unsub:
            self._unsub()
        self._unsub = async_track_time_interval(self.hass, update_interval, timedelta(seconds=next_interval_seconds))


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
        return f"OneBusAway {self.stop_id} Arrival {self.index}"

    @property
    def extra_state_attributes(self):
        """Return additional metadata for this bus arrival."""
        if not self.arrival_info:
            return {}
        return {
            "arrival_time": self.arrival_info["type"],
            "route_name": self.arrival_info["routeShortName"],
        }
        
    @property
    def icon(self) -> str:
        """Return the icon for this sensor based on arrival type."""
        if self.arrival_info:
            if self.arrival_info["type"].lower() == "predicted":
                return "mdi:rss"
            else:
                return "mdi:timetable"
        return "mdi:train-bus"

class OneBusAwaySituationSensor(SensorEntity):
    """Sensor to display the count of situations and their details."""

    def __init__(self, stop_id, situations) -> None:
        """Initialize the situation sensor."""
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
        """Update the situation details and refresh state."""
        self.situations = situations
        self.async_write_ha_state()
    
    @property
    def native_value(self) -> int:
        """Return the count of situations."""
        return len(self.situations)

    @property
    def name(self) -> str:
        """Friendly name for the sensor."""
        return f"Stop {self.stop_id} Situations"

    @property
    def icon(self) -> str:
        """Icon for the sensor."""
        return "mdi:alert-circle-check"

    def _sanitize_text(self, text: str) -> str:
        """Sanitize text by removing all extraneous escape characters, including line breaks."""
        # Replace multiple spaces from \r\n sequences and remove all newlines
        return re.sub(r"[\r\n]+", " ", text).strip()

    @property
    def extra_state_attributes(self):
        """Return additional metadata for situations."""
        attributes = {}
        
        markdown_lines = []
        for index, situation in enumerate(self.situations):
            severity = situation.get("severity", "Unknown")
            reason = self._sanitize_text(situation.get("reason", "Not specified"))
            summary = self._sanitize_text(situation.get("summary", {}).get("value", "")).replace("\n", " ").strip()
            url = situation.get("url", {}).get("value", "")
    
            if summary:
                if index > 0:
                    markdown_lines.append("\n---\n")
                markdown_lines.append(
                    f"**Severity:** {severity}  \n"
                    f"**Reason:** {reason}  \n"
                    f"[{summary}]({url})" if url else summary
                )
    
        attributes["markdown_content"] = "\n".join(markdown_lines)
        return attributes
        
class OneBusAwayRefreshSensor(SensorEntity):
    """Sensor to display the next refresh timestamp."""

    def __init__(self, stop_id) -> None:
        """Initialize the refresh sensor."""
        self.stop_id = stop_id
        self._attr_unique_id = f"{stop_id}_next_refresh"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, stop_id)},
            name=f"Stop {stop_id}",
            model=VERSION,
            manufacturer=NAME,
        )
        self._attr_device_class = SensorDeviceClass.TIMESTAMP  # Enables relative time in UI
        self.entity_id = f"sensor.onebusaway_{stop_id}_next_refresh"
        self._next_refresh = None

    def update_refresh_time(self, next_refresh_time: datetime):
        """Update the refresh timestamp."""
        self._next_refresh = next_refresh_time
        self.async_write_ha_state()

    @property
    def native_value(self) -> datetime | None:
        """Return the next refresh timestamp."""
        return self._next_refresh

    @property
    def name(self) -> str:
        """Friendly name for the sensor."""
        return f"Stop {self.stop_id} Next Refresh"

    @property
    def icon(self) -> str:
        """Icon for the sensor."""
        return "mdi:refresh"
