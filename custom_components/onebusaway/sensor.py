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
            """Extract time, type, route name, trip headsign, and compute schedule deviation."""
            predicted = d.get("predictedArrivalTime")
            scheduled = d.get("scheduledArrivalTime")
            trip_headsign = d.get("tripHeadsign", "Unknown")
            route_name = d.get("routeShortName", "Unknown Route")
            route_id = d.get("routeId")
        
            # Filter out routes not in selected list
            if selected_routes and route_id not in selected_routes:
                return None
        
            # Only include if either time is in the future
            if (predicted and predicted > current) or (scheduled and scheduled > current):
                primary_time = (predicted or scheduled) / 1000
                deviation = (
                    (predicted - scheduled) // 1000
                    if predicted and scheduled else None
                )
                return {
                    "time": primary_time,
                    "type": "Predicted" if predicted and predicted > current else "Scheduled",
                    "headsign": trip_headsign,
                    "routeShortName": route_name,
                    "schedule_deviation": deviation,
                }
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
        """Schedule sensor updates with controlled stepwise backoff and repeats."""
        async def update_interval(_):
            _LOGGER.debug("Updating stop %s", self.stop_id)
            await self.async_update()
            await self.schedule_updates()
    
        arrivals = self.compute_arrivals(time())
        seconds_until_arrival = arrivals[0]["time"] - time() if arrivals else float("inf")
    
        polling_tiers = [30, 60, 90, 180, 300]
        repeat_limits = [2, 1, 0, 0, 0]  # Number of repeats for each tier
        max_interval = 300
    
        current_interval = getattr(self, "_current_interval", max_interval)
        tier_index = polling_tiers.index(current_interval) if current_interval in polling_tiers else len(polling_tiers) - 1
        repeat_count = getattr(self, "_repeat_count", 0)
    
        # Determine target tier index
        if seconds_until_arrival <= 3 * 60:
            target_index = 0
        elif seconds_until_arrival <= 6 * 60:
            target_index = 1
        elif seconds_until_arrival <= 10 * 60:
            target_index = 2
        elif seconds_until_arrival <= 15 * 60:
            target_index = 3
        else:
            target_index = 4
    
        # Step logic with repeats only when stepping up
        if target_index > tier_index:
            if repeat_count < repeat_limits[tier_index]:
                next_interval_seconds = current_interval
                self._repeat_count = repeat_count + 1
            else:
                tier_index = min(tier_index + 1, len(polling_tiers) - 1)
                next_interval_seconds = polling_tiers[tier_index]
                self._repeat_count = 0
        elif target_index < tier_index:
            tier_index = max(tier_index - 1, 0)
            next_interval_seconds = polling_tiers[tier_index]
            self._repeat_count = 0
        else:
            next_interval_seconds = current_interval
            self._repeat_count = repeat_count
    
        self._current_interval = next_interval_seconds
        next_update_time = datetime.now(timezone.utc) + timedelta(seconds=next_interval_seconds)
        self.refresh_sensor.update_refresh_time(next_update_time)
    
        _LOGGER.debug(
            "Next update for stop %s in %d seconds (next arrival in %.1f min, repeat count %d)",
            self.stop_id,
            next_interval_seconds,
            seconds_until_arrival / 60,
            self._repeat_count
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
            route = self.arrival_info.get("routeShortName", "")
            headsign = self.arrival_info.get("headsign", "Unknown")
            deviation = self.arrival_info.get("schedule_deviation")
    
            # Format deviation
            if deviation is None:
                deviation_str = ""
            elif deviation == 0:
                deviation_str = "(on time)"
            elif deviation > 0:
                deviation_str = f"({deviation // 60} min late)"
            else:
                deviation_str = f"({abs(deviation) // 60} min early)"
    
            # Skip route if stop_id starts with "95_"
            if self.stop_id.startswith("95_"):
                return f"to {headsign} {deviation_str}".strip()
            else:
                return f"{route} to {headsign} {deviation_str}".strip()
    
        return f"OneBusAway {self.stop_id} Arrival {self.index}"

    
    @property
    def extra_state_attributes(self):
        """Return additional metadata for this bus arrival."""
        if not self.arrival_info:
            return {}
    
        deviation = self.arrival_info.get("schedule_deviation")
        if deviation is None:
            deviation_str = "Unknown"
            deviation_minutes = None
        elif deviation == 0:
            deviation_str = "On time"
            deviation_minutes = 0.0
        elif deviation > 0:
            deviation_str = f"{deviation // 60} min late"
            deviation_minutes = round(deviation / 60, 1)
        else:
            deviation_str = f"{abs(deviation) // 60} min early"
            deviation_minutes = round(deviation / 60, 1)  # negative value
    
        return {
            "arrival_type": self.arrival_info.get("type"),  # "Predicted" or "Scheduled"
            "route_name": self.arrival_info.get("routeShortName"),
            "headsign": self.arrival_info.get("headsign"),
            "schedule_deviation": deviation_str,
            "schedule_deviation_minutes": deviation_minutes,  # decimal minutes, 1 decimal place
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
            summary = situation.get("summary", {}).get("value", "") or ""
            url = situation.get("url", {}).get("value", "") or ""
    
            # Separator between situations
            if index > 0:
                markdown_lines.append("\n---\n")
    
            # Summary (bold, linked if URL present)
            if summary:
                markdown_lines.append(f"**[{summary}]({url})**" if url else f"**{summary}**")
    
            # --- Description handling: split BEFORE sanitizing ---
            raw_description = situation.get("description", {}).get("value", "") or ""
            normalized = raw_description.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
            raw_lines = [ln for ln in normalized.split("\n") if ln.strip()]
    
            if raw_lines:
                # First line: plain text (no bullet)
                header = self._sanitize_text(raw_lines[0]).strip()
                if header:
                    markdown_lines.append(header)
    
                # Remaining lines: bullets
                for ln in raw_lines[1:]:
                    safe_line = self._sanitize_text(ln).strip()
                    if safe_line:
                        markdown_lines.append(f"- {safe_line}")
    
        if markdown_lines:
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
