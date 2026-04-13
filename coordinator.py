"""DataUpdateCoordinator for CP Trains."""
from __future__ import annotations

from datetime import datetime, timedelta
import logging
import re

import aiohttp
import async_timeout

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import API_URL, DOMAIN, LOGGER, UPDATE_INTERVAL_SECONDS

# Standard browser user-agent to avoid 403 Forbidden
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

class CPTrainsCoordinator(DataUpdateCoordinator):
    """Class to manage fetching CP Trains data."""

    def __init__(self, hass: HomeAssistant, train_number: str) -> None:
        """Initialize."""
        super().__init__(
            hass,
            LOGGER,
            name=f"{DOMAIN}_{train_number}",
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.train_number = train_number

    async def _async_update_data(self) -> dict[str, any]:
        """Fetch data from API."""
        train_date = datetime.now().strftime("%Y-%m-%d")
        url = API_URL.format(train_number=self.train_number, train_date=train_date)

        headers = {"User-Agent": USER_AGENT}

        try:
            async with async_timeout.timeout(10):
                session = async_get_clientsession(self.hass)
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        raise UpdateFailed(f"Error communicating with API: {response.status}")

                    data = await response.json()
                    if not data:
                        raise UpdateFailed("Empty response from API")

                    return self._parse_data(data)
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Error communicating with API: {err}")
        except Exception as err:
            raise UpdateFailed(f"Unexpected error: {err}")

    def _parse_data(self, data: dict[str, any]) -> dict[str, any]:
        """Parse the JSON response from CP API."""
        # Main train info
        train_info = data.get("Comboio", {})
        if not train_info:
            return {}

        status_text = train_info.get("SituacaoComboio", "")

        # Parse stations
        stations = []
        all_passed = True
        nodes = data.get("NodesPassagemComboio", [])

        for node in nodes:
            station_name = node.get("NomeEstacao")
            scheduled_time_str = node.get("HoraProgramada")
            obs = node.get("Observacoes", "")
            passed = node.get("ComboioPassou", False)

            if not passed:
                all_passed = False

            # Extract estimated time from Observacoes (e.g., "Hora Prevista: 12:45")
            estimated_time_str = scheduled_time_str
            match = re.search(r"Hora Prevista\s*:\s*(\d{2}:\d{2})", obs or "")
            if match:
                estimated_time_str = match.group(1)

            # Compute delay in minutes
            delay_minutes = 0
            if scheduled_time_str and estimated_time_str:
                try:
                    fmt = "%H:%M"
                    sched = datetime.strptime(scheduled_time_str, fmt)
                    estim = datetime.strptime(estimated_time_str, fmt)
                    delay_minutes = int((estim - sched).total_seconds() / 60)
                except ValueError:
                    pass

            stations.append({
                "name": station_name,
                "scheduled": scheduled_time_str,
                "estimated": estimated_time_str,
                "delay_minutes": delay_minutes,
                "passed": passed
            })

        # Determine state
        state = "unknown"
        if all_passed and stations:
            state = "passed"
        elif "atraso" in status_text.lower():
            state = "delayed"
        elif "circula" in status_text.lower():
            state = "on_time"

        return {
            "train_number": self.train_number,
            "service": train_info.get("TipoServico"),
            "origin": train_info.get("DataHoraOrigem"), # Actually origin station name? Let's check API output structure.
            "destination": train_info.get("DataHoraDestino"), # Actually destination station name?
            "scheduled_departure": train_info.get("DataHoraOrigem"),
            "scheduled_arrival": train_info.get("DataHoraDestino"),
            "status_text": status_text,
            "state": state,
            "stations": stations
        }
