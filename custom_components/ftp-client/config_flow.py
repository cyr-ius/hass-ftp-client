"""Config flow for the FTPClient integration."""

from __future__ import annotations

import logging
from ssl import SSLError
from typing import Any

from aioftp.errors import AIOFTPException
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_SSL, CONF_USERNAME
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import CONF_BACKUP_PATH, DEFAULT_BACKUP_PATH, DOMAIN
from .helpers import FTPClient

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD,
            )
        ),
        vol.Required(CONF_BACKUP_PATH, default=DEFAULT_BACKUP_PATH): str,
        vol.Optional(CONF_SSL, default=True): bool,
    }
)


class FTPDriveConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for FTPClient."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            ftp = FTPClient(
                host=user_input[CONF_HOST],
                username=user_input[CONF_USERNAME],
                password=user_input[CONF_PASSWORD],
                ssl=user_input.get(CONF_SSL, True),
            )

            # Check if we can connect to the WebDAV server
            # .check() already does the most of the error handling and will return True
            # if we can access the root directory
            try:
                client = await ftp.async_connect()
                result = await client.list()
            except AIOFTPException:
                errors["base"] = "invalid_auth"
            except SSLError:
                errors["base"] = "ssl_error"
            except Exception:
                _LOGGER.exception("Unexpected error")
                errors["base"] = "unknown"
            else:
                if result:
                    self._async_abort_entries_match(
                        {
                            CONF_HOST: user_input[CONF_HOST],
                            CONF_USERNAME: user_input[CONF_USERNAME],
                        }
                    )
                    await client.quit()

                    return self.async_create_entry(
                        title=f"{user_input[CONF_USERNAME]}@{user_input[CONF_HOST]}",
                        data=user_input,
                    )

                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )
