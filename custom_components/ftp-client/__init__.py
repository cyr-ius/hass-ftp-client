"""The FTPClient integration."""

from __future__ import annotations

import logging

from aioftp.errors import AIOFTPException

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_SSL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady

from .const import (
    CONF_BACKUP_PATH,
    DATA_BACKUP_AGENT_LISTENERS,
    DEFAULT_BACKUP_PATH,
    DOMAIN,
)
from .helpers import FTPClient

type FTPDriveConfigEntry = ConfigEntry[FTPClient]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: FTPDriveConfigEntry) -> bool:
    """Set up FTPClient from a config entry."""
    ftp = FTPClient(
        host=entry.data[CONF_HOST],
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        ssl=entry.data.get(CONF_SSL, True),
    )

    try:
        client = await ftp.async_connect()
        result = await client.list()
    except AIOFTPException as err:
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="invalid_username_password",
        ) from err

    # Check if we can connect to the FTP server
    # and access the root directory
    if not result:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
        )

    path = entry.data.get(CONF_BACKUP_PATH, DEFAULT_BACKUP_PATH)

    # Ensure the backup directory exists
    if not await ftp.async_ensure_path_exists(path):
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_access_or_create_backup_path",
        )

    entry.runtime_data = ftp
    await client.quit()

    def async_notify_backup_listeners() -> None:
        for listener in hass.data.get(DATA_BACKUP_AGENT_LISTENERS, []):
            listener()

    entry.async_on_unload(entry.async_on_state_change(async_notify_backup_listeners))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: FTPDriveConfigEntry) -> bool:
    """Unload a FTPClient config entry."""
    ftp = entry.runtime_data
    await ftp.async_close()
    return True
