"""Config flow for CP Trains integration."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN, CONF_TRAIN_NUMBER

class CPTrainsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for CP Trains."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors = {}
        if user_input is not None:
            # You might want to add validation here to check if the train number exists
            # but for now, we'll just create the entry.
            return self.async_create_entry(
                title=f"Train {user_input[CONF_TRAIN_NUMBER]}",
                data=user_input,
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TRAIN_NUMBER): str,
                }
            ),
            errors=errors,
        )
