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

    async def async_get_data(self, stop_id: str | None = None) -> any:
        """Get data from the API for the specified stop ID."""
        stop_id = stop_id if stop_id else self._stop
        return await self._api_wrapper(
            method="get",
            url=f"{self._url}/where/arrivals-and-departures-for-stop/{stop_id}.json?key={self._key}",
        )

    async def async_get_stop_data(self, stop_id: str | None = None) -> any:
        """Get full stop data from the API."""
        stop_id = stop_id if stop_id else self._stop
        return await self._api_wrapper(
            method="get",
            url=f"{self._url}/where/stop/{stop_id}.json?key={self._key}",
        )

    
    async def _api_wrapper(
        self,
        method: str,
        url: str,
        data: dict | None = None,
        headers: dict | None = None,
    ) -> any:
        """Get information from the API with rate limit handling."""
        max_retries = 4
        backoff_factor = 3  # Exponential backoff factor

        for attempt in range(max_retries):
            try:
                async with async_timeout.timeout(10):
                    response = await self._session.request(
                        method=method,
                        url=url,
                        headers=headers,
                        json=data,
                    )
                    if response.status == 429:
                        if attempt < max_retries - 1:
                            wait_time = backoff_factor ** attempt
                            print(f"Rate limited. Retrying in {wait_time} seconds...")
                            await asyncio.sleep(wait_time)
                            continue
                        else:
                            raise OneBusAwayApiClientCommunicationError(
                                "Exceeded maximum retry attempts due to rate limiting."
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
