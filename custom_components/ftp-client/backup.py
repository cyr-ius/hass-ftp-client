"""Support for FTPClient backup."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Coroutine
from functools import wraps
from json import loads as json_loads
import logging
from typing import Any, Concatenate

from aioftp import AIOFTPException
from propcache.api import cached_property

from config.custom_components.hacs.utils.backup import DEFAULT_BACKUP_PATH
from homeassistant.components.backup import (
    AgentBackup,
    BackupAgent,
    BackupAgentError,
    BackupNotFound,
    suggested_filename,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.json import json_dumps

from . import FTPDriveConfigEntry
from .const import CONF_BACKUP_PATH, DATA_BACKUP_AGENT_LISTENERS, DOMAIN
from .helpers import json_to_stream

_LOGGER = logging.getLogger(__name__)


def suggested_filenames(backup: AgentBackup) -> tuple[str, str]:
    """Return the suggested filenames for the backup and metadata."""
    base_name = suggested_filename(backup).rsplit(".", 1)[0]
    return f"{base_name}.tar", f"{base_name}.metadata.json"


async def async_get_backup_agents(
    hass: HomeAssistant,
) -> list[BackupAgent]:
    """Return a list of backup agents."""
    entries: list[FTPDriveConfigEntry] = hass.config_entries.async_loaded_entries(
        DOMAIN
    )
    return [FTPDriveBackupAgent(hass, entry) for entry in entries]


@callback
def async_register_backup_agents_listener(
    hass: HomeAssistant,
    *,
    listener: Callable[[], None],
    **kwargs: Any,
) -> Callable[[], None]:
    """Register a listener to be called when agents are added or removed.

    :return: A function to unregister the listener.
    """
    hass.data.setdefault(DATA_BACKUP_AGENT_LISTENERS, []).append(listener)

    @callback
    def remove_listener() -> None:
        """Remove the listener."""
        hass.data[DATA_BACKUP_AGENT_LISTENERS].remove(listener)
        if not hass.data[DATA_BACKUP_AGENT_LISTENERS]:
            del hass.data[DATA_BACKUP_AGENT_LISTENERS]

    return remove_listener


def handle_backup_errors[_R, **P](
    func: Callable[Concatenate[FTPDriveBackupAgent, P], Coroutine[Any, Any, _R]],
) -> Callable[Concatenate[FTPDriveBackupAgent, P], Coroutine[Any, Any, _R]]:
    """Handle backup errors."""

    @wraps(func)
    async def wrapper(
        self: FTPDriveBackupAgent, *args: P.args, **kwargs: P.kwargs
    ) -> _R:
        try:
            return await func(self, *args, **kwargs)
        except AIOFTPException as err:
            _LOGGER.debug("Full error: %s", err, exc_info=True)
            raise BackupAgentError(f"Backup operation failed: {err}") from err

    return wrapper


# pyright: reportIncompatibleMethodOverride=none


class FTPDriveBackupAgent(BackupAgent):
    """Backup agent interface."""

    domain = DOMAIN

    def __init__(self, hass: HomeAssistant, entry: FTPDriveConfigEntry) -> None:
        """Initialize the FTPClient backup agent."""
        super().__init__()
        self._hass = hass
        self._entry = entry
        self._ftp = entry.runtime_data
        self.name = entry.title
        self.unique_id = entry.entry_id
        self._cache_metadata_files: dict[str, AgentBackup] = {}

    @cached_property
    def _backup_path(self) -> str:
        """Return the path to the backup."""
        return self._entry.data.get(CONF_BACKUP_PATH, DEFAULT_BACKUP_PATH)

    @handle_backup_errors
    async def async_download_backup(
        self,
        backup_id: str,
        **kwargs: Any,
    ) -> AsyncIterator[bytes]:
        """Download a backup file.

        :param backup_id: The ID of the backup that was returned in async_list_backups.
        :return: An async iterator that yields bytes.
        """
        backup = await self._find_backup_by_id(backup_id)
        client = await self._ftp.async_connect()
        stream = await client.download_stream(
            f"{self._backup_path}/{suggested_filename(backup)}"
        )

        async def stream_chunks() -> AsyncIterator[bytes]:
            async for chunk in stream.iter_by_block():
                yield chunk

        return stream_chunks()

    @handle_backup_errors
    async def async_upload_backup(  # type: ignore=["reportIncompatibleMethodOverride"]
        self,
        *,
        open_stream: Callable[[], Coroutine[Any, Any, AsyncIterator[bytes]]],
        backup: AgentBackup,
        **kwargs: Any,
    ) -> None:
        """Upload a backup.

        :param open_stream: A function returning an async iterator that yields bytes.
        :param backup: Metadata about the backup that should be uploaded.
        """
        (filename_tar, filename_meta) = suggested_filenames(backup)

        client = await self._ftp.async_connect()

        stream_iter = await open_stream()
        tar_path = f"{self._backup_path}/{filename_tar}"
        stream = await client.upload_stream(tar_path)
        async for chunk in stream_iter:
            await stream.write(chunk)
        await stream.finish()

        metadata_content = json_dumps(backup.as_dict())
        stream_iter = json_to_stream(metadata_content)
        meta_path = f"{self._backup_path}/{filename_meta}"
        stream = await client.upload_stream(meta_path)
        async for chunk in stream_iter:
            await stream.write(chunk)
        await stream.finish()

        await client.quit()

    @handle_backup_errors
    async def async_delete_backup(  # type: ignore=["reportIncompatibleMethodOverride"]
        self,
        backup_id: str,
        **kwargs: Any,
    ) -> None:
        """Delete a backup file.

        :param backup_id: The ID of the backup that was returned in async_list_backups.
        """
        backup = await self._find_backup_by_id(backup_id)

        (filename_tar, filename_meta) = suggested_filenames(backup)
        client = await self._ftp.async_connect()
        await client.remove(f"{self._backup_path}/{filename_tar}")
        await client.remove(f"{self._backup_path}/{filename_meta}")
        await client.quit()

    @handle_backup_errors
    async def async_list_backups(self, **kwargs: Any) -> list[AgentBackup]:
        """List backups."""
        return list((await self._async_list_backups()).values())

    async def _async_list_backups(self, **kwargs: Any) -> dict[str, AgentBackup]:
        """List metadata files with a cache."""

        client = await self._ftp.async_connect()

        async def _download_metadata(path: str) -> AgentBackup:
            """Download metadata file."""
            stream = await client.download_stream(path)
            chunks = [chunk async for chunk in stream.iter_by_block(65536)]
            await stream.finish()
            metadata_bytes = b"".join(chunks)
            metadata = json_loads(metadata_bytes.decode("utf-8"))
            return AgentBackup.from_dict(metadata)

        async def _list_metadata_files() -> dict[str, AgentBackup]:
            """List metadata files."""
            files = await client.list(self._backup_path)
            metadata_files = {}
            for posix_path, _ in files:
                file_name = str(posix_path)
                if file_name.endswith(".metadata.json"):
                    metadata_content = await _download_metadata(file_name)
                    if metadata_content:
                        metadata_files[metadata_content.backup_id] = metadata_content
            return metadata_files

        self._metadata_files = await _list_metadata_files()
        await client.quit()
        return self._metadata_files

    @handle_backup_errors
    async def async_get_backup(
        self,
        backup_id: str,
        **kwargs: Any,
    ) -> AgentBackup:
        """Return a backup."""
        return await self._find_backup_by_id(backup_id)

    async def _find_backup_by_id(self, backup_id: str) -> AgentBackup:
        """Find a backup by its backup ID on remote."""
        metadata_files = await self._async_list_backups()
        if metadata_file := metadata_files.get(backup_id):
            return metadata_file

        raise BackupNotFound(f"Backup {backup_id} not found")
