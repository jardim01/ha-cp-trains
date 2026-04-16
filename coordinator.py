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
from homeassistant.util import dt as dt_util

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
        self._scheduled_departure: datetime | None = None
        self._scheduled_arrival: datetime | None = None

    async def _async_update_data(self) -> dict[str, any]:
        """Fetch data from API."""
        now = dt_util.now()

        # Check if we should skip update based on schedule
        if self._scheduled_departure and self._scheduled_arrival:
            start_window = self._scheduled_departure - timedelta(minutes=30)
            end_window = self._scheduled_arrival + timedelta(minutes=10)

            if not (start_window <= now <= end_window):
                LOGGER.debug(
                    "Skipping update for train %s: outside of operating window",
                    self.train_number
                )
                if now > end_window and self.data:
                    self.data["state"] = "passed"
                return self.data

        train_date = now.strftime("%Y-%m-%d")
        url = API_URL.format(train_number=self.train_number, train_date=train_date)
        headers = {"User-Agent": USER_AGENT}

        try:
            async with async_timeout.timeout(10):
                session = async_get_clientsession(self.hass)
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        raise UpdateFailed(f"Error communicating with API: {response.status}")

                    data = await response.json()
                    if not data or "response" not in data:
                        raise UpdateFailed("Unexpected response format from API")

                    parsed_data = self._parse_data(data["response"])

                    # Store schedule for smart polling (convert ISO strings back to aware datetimes)
                    if parsed_data.get("scheduled_departure"):
                        self._scheduled_departure = dt_util.parse_datetime(parsed_data["scheduled_departure"])
                        self._scheduled_arrival = dt_util.parse_datetime(parsed_data["scheduled_arrival"])

                    return parsed_data
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Error communicating with API: {err}")
        except Exception as err:
            raise UpdateFailed(f"Unexpected error: {err}")

    def _to_utc_iso(self, date_str: str | None) -> str | None:
        """Convert CP date string (Lisbon time) to UTC ISO format."""
        if not date_str:
            return None
        try:
            # Parse CP format: 13/04/2026 18:30:00
            naive_dt = datetime.strptime(date_str, "%d/%m/%Y %H:%M:%S")
            # Localize to Lisbon time (CP API uses Lisbon time)
            # Use dt_util to handle timezones correctly in HA
            aware_dt = dt_util.as_utc(dt_util.now().replace(
                year=naive_dt.year,
                month=naive_dt.month,
                day=naive_dt.day,
                hour=naive_dt.hour,
                minute=naive_dt.minute,
                second=naive_dt.second,
                microsecond=0
            ).astimezone(dt_util.get_time_zone("Europe/Lisbon")))

            # Re-calculating correctly: naive -> localized -> utc
            # Actually, let's use a cleaner way with dt_util
            tz = dt_util.get_time_zone("Europe/Lisbon")
            localized_dt = datetime(
                naive_dt.year, naive_dt.month, naive_dt.day,
                naive_dt.hour, naive_dt.minute, naive_dt.second,
                tzinfo=tz
            )
            return dt_util.as_utc(localized_dt).isoformat().replace("+00:00", "Z")
        except (ValueError, TypeError):
            return date_str

    def _parse_data(self, data: dict[str, any]) -> dict[str, any]:
        """Parse the JSON response from CP API."""
        status_text = data.get("SituacaoComboio", "")

        # Extract global delay from status text (e.g., "atraso de 14 min")
        delay = 0
        delay_match = re.search(r"(\d+)\s*min", status_text)
        if delay_match:
            delay = int(delay_match.group(1))

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

            # Determine estimated time
            estimated_time_str = scheduled_time_str
            if not passed:
                # Check for specific station observation first
                match = re.search(r"Hora Prevista\s*:\s*(\d{2}:\d{2})", obs)
                if match:
                    estimated_time_str = match.group(1)
                elif delay > 0:
                    # Apply global delay to schedule
                    try:
                        t = datetime.strptime(scheduled_time_str, "%H:%M")
                        new_t = t + timedelta(minutes=delay)
                        estimated_time_str = new_t.strftime("%H:%M")
                    except (ValueError, TypeError):
                        pass

            stations.append({
                "name": station_name,
                "scheduled": scheduled_time_str,
                "estimated": estimated_time_str,
                "passed": passed
            })

        # Determine state
        if all_passed and stations:
            state = "passed"
        elif status_text == "Programado":
            state = "scheduled"
        elif delay > 0:
            state = "delayed"
        else:
            state = "on_time"

        return {
            "train_number": self.train_number,
            "service": data.get("TipoServico"),
            "origin": data.get("Origem"),
            "destination": data.get("Destino"),
            "scheduled_departure": self._to_utc_iso(data.get("DataHoraOrigem")),
            "scheduled_arrival": self._to_utc_iso(data.get("DataHoraDestino")),
            "status_text": status_text if status_text else "Em circulação",
            "delay": delay,
            "state": state,
            "stations": stations
        }
