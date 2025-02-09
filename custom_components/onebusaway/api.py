"""Sample API Client."""
from __future__ import annotations

import asyncio
import socket

import aiohttp
import async_timeout


class OneBusAwayApiClientError(Exception):
    """Exception to indicate a general API error."""


class OneBusAwayApiClientCommunicationError(OneBusAwayApiClientError):
    """Exception to indicate a communication error."""


class OneBusAwayApiClientAuthenticationError(OneBusAwayApiClientError):
    """Exception to indicate an authentication error."""


class OneBusAwayApiClient:
    """Sample API Client."""

    def __init__(
        self,
        url: str,
        key: str,
        stop: str,
        session: aiohttp.ClientSession,
    ) -> None:
        """Sample API Client."""
        self._url = url
        self._key = key
        self._stop = stop
        self._session = session

    async def async_get_data(self) -> any:
        """Get data from the API."""
        return await self._api_wrapper(
            method="get",
            url=f"{self._url}/where/arrivals-and-departures-for-stop/{self._stop}.json?key={self._key}",
        )

    async def _api_wrapper(
        self,
        method: str,
        url: str,
        data: dict | None = None,
        headers: dict | None = None,
    ) -> any:
        """Get information from the API."""
        try:
            async with async_timeout.timeout(10):
                response = await self._session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=data,
                )
                if response.status in (401, 403):
                    raise OneBusAwayApiClientAuthenticationError(
                        "Invalid credentials",
                    )
                response.raise_for_status()
                return await response.json()

        except asyncio.TimeoutError as exception:
            raise OneBusAwayApiClientCommunicationError(
                "Timeout error fetching information",
            ) from exception
        except (aiohttp.ClientError, socket.gaierror) as exception:
            raise OneBusAwayApiClientCommunicationError(
                "Error fetching information",
            ) from exception
        except Exception as exception:  # pylint: disable=broad-except
            raise OneBusAwayApiClientError(
                "Something really wrong happened!"
            ) from exception
