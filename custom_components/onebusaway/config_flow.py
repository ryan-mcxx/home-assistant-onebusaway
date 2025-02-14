from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_URL, CONF_ID, CONF_TOKEN
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import (
    OneBusAwayApiClient,
    OneBusAwayApiClientAuthenticationError,
    OneBusAwayApiClientCommunicationError,
    OneBusAwayApiClientError,
)
from .const import DOMAIN, LOGGER


class OneBusAwayFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for OneBusAway."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict | None = None,
    ) -> config_entries.FlowResult:
        """Handle a flow initialized by the user."""
        _errors = {}
    
        # Retrieve existing entries
        existing_entries = self._async_current_entries()
        existing_token = None
    
        # Look for an existing CONF_TOKEN
        for entry in existing_entries:
            if CONF_TOKEN in entry.data:
                existing_token = entry.data[CONF_TOKEN]
                break  # Use the first found token
    
        if user_input is not None:
            try:
                arrival = await self._test_url(
                    url=user_input[CONF_URL],
                    key=user_input[CONF_TOKEN] if user_input.get(CONF_TOKEN) else existing_token,
                    stop=user_input[CONF_ID],
                )
            except OneBusAwayApiClientAuthenticationError as exception:
                LOGGER.warning(exception)
                _errors["base"] = "auth"
            except OneBusAwayApiClientCommunicationError as exception:
                LOGGER.error(exception)
                _errors["base"] = "connection"
            except OneBusAwayApiClientError as exception:
                LOGGER.exception(exception)
                _errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=arrival["stopId"],
                    data={
                        CONF_URL: user_input[CONF_URL],
                        CONF_ID: user_input[CONF_ID],
                        CONF_TOKEN: existing_token or user_input[CONF_TOKEN],
                    },
                )
    
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_URL, default="https://api.pugetsound.onebusaway.org/api"
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.URL),
                    ),
                    vol.Optional(
                        CONF_TOKEN, default=existing_token 
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT),
                    ),
                    vol.Required(CONF_ID, default="40_55778"): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT),
                    ),
                }
            ),
            errors=_errors,
        )

    async def async_step_routes(self, user_input=None):
        """Handle route selection."""
        if not self.stop_data:
            return self.async_abort(reason="missing_stop_data")

        # Extract available routes
        routes = self.stop_data["data"]["references"]["routes"]
        route_options = {route["id"]: f"{route['shortName']} ({route['id']})" for route in routes}

        if user_input is not None:
            # Store selected routes
            selected_routes = [
                {"id": route_id, "shortName": route_options[route_id]}
                for route_id in user_input["selected_routes"]
            ]
            self.user_input["selected_routes"] = selected_routes

            # Create the final entry
            return self.async_create_entry(
                title=self.stop_data["data"]["entry"]["name"],
                data=self.user_input,
            )

        return self.async_show_form(
            step_id="routes",
            data_schema=vol.Schema(
                {
                    vol.Required("selected_routes", default=[]): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"value": route_id, "label": route_options[route_id]}
                                for route_id in route_options
                            ],
                            multiple=True,
                        )
                    )
                }
            ),
        )
    
    async def _test_url(self, url: str, key: str, stop: str):
        """Fetch and store stop data."""
        client = OneBusAwayApiClient(
            url=url,
            key=key,
            stop=stop,
            session=async_create_clientsession(self.hass),
        )
        json = await client.async_get_stop_data()  # Call a new method to fetch stop data
    
        # Store the full stop data for later use in async_step_routes
        self.stop_data = json  
    
        return json["data"]["entry"]["arrivalsAndDepartures"][0]

