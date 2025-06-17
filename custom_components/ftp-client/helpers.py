"""Helper functions for the FTPClient component."""

from __future__ import annotations

from collections.abc import AsyncIterator

from aioftp import Client, StatusCodeError

from homeassistant.core import callback


class FTPClient:
    """Client."""

    def __init__(self, *, host: str, username: str, password: str, ssl: bool = False):
        """Initialize."""
        self.host = host
        self._username = username
        self._password = password
        self._ssl = ssl
        self.client = Client(ssl=self._ssl)

    @callback
    async def async_connect(self) -> Client:
        """Create a FTP client."""
        await self.client.connect(self.host)
        await self.client.login(self._username, self._password)
        return self.client

    async def async_close(self) -> None:
        """Close ftp session."""
        await self.client.quit()
        self.client.close()

    async def async_ensure_path_exists(self, path: str) -> bool:
        """Ensure that a path exists recursively on the FTP server."""
        try:
            await self.client.is_dir(path)
        except StatusCodeError:
            if not await self.client.make_directory(path):
                return False
        return True


def json_to_stream(json_str: str, chunk_size: int = 8192) -> AsyncIterator[bytes]:
    """Convert a JSON string into an async iterator of bytes."""

    async def generator() -> AsyncIterator[bytes]:
        encoded = json_str.encode("utf-8")
        for i in range(0, len(encoded), chunk_size):
            yield encoded[i : i + chunk_size]

    return generator()
