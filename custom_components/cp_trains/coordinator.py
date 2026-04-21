"""DataUpdateCoordinator for CP Trains."""
from __future__ import annotations

import asyncio
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

            # Use last known delay to adjust end window
            last_delay = self.data.get("delay", 0) if self.data else 0
            end_window = self._scheduled_arrival + timedelta(minutes=last_delay + 10)

            if not (start_window <= now <= end_window):
                LOGGER.debug(
                    "Skipping update for train %s: outside of operating window (delay: %s)",
                    self.train_number,
                    last_delay
                )
                if now > end_window and self.data:
                    self.data["state"] = "passed"
                return self.data

        train_date = now.strftime("%Y-%m-%d")
        url = API_URL.format(train_number=self.train_number, train_date=train_date)
        headers = {"User-Agent": USER_AGENT}

        # Retry logic: 3 attempts with 30s timeout
        for attempt in range(3):
            try:
                async with async_timeout.timeout(30):
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
            except (aiohttp.ClientError, asyncio.TimeoutError, UpdateFailed) as err:
                if attempt == 2:
                    raise UpdateFailed(f"Error communicating with API after 3 attempts: {err}")
                LOGGER.warning("Attempt %s failed for train %s: %s. Retrying...", attempt + 1, self.train_number, err)
                await asyncio.sleep(2)
            except Exception as err:
                raise UpdateFailed(f"Unexpected error: {err}")

    def _to_utc_iso(self, date_str: str | None, base_date: datetime | None = None) -> str | None:
        """Convert CP date string (Lisbon time) to UTC ISO format."""
        if not date_str:
            return None
        try:
            tz = dt_util.get_time_zone("Europe/Lisbon")
            if " " in date_str:
                # Full date/time: 13/04/2026 18:30:00
                naive_dt = datetime.strptime(date_str, "%d/%m/%Y %H:%M:%S")
            elif base_date:
                # Just time: 18:30
                t = datetime.strptime(date_str, "%H:%M")
                naive_dt = base_date.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
            else:
                return date_str

            localized_dt = naive_dt.replace(tzinfo=tz)
            return dt_util.as_utc(localized_dt).isoformat().replace("+00:00", "Z")
        except (ValueError, TypeError):
            return date_str

    def _parse_data(self, data: dict[str, any]) -> dict[str, any]:
        """Parse the JSON response from CP API."""
        status_text = data.get("SituacaoComboio", "") or ""

        # Extract global delay from status text (e.g., "atraso de 14 min")
        delay = 0
        delay_match = re.search(r"(\d+)\s*min", status_text)
        if delay_match:
            delay = int(delay_match.group(1))

        # Base date for station times
        base_date = None
        dep_str = data.get("DataHoraOrigem")
        if dep_str:
            base_date = datetime.strptime(dep_str, "%d/%m/%Y %H:%M:%S")

        # Parse stations
        stations = []
        all_passed = True
        nodes = data.get("NodesPassagemComboio", [])

        last_sched_naive = None
        last_estim_naive = None
        current_sched_date = base_date
        current_estim_date = base_date

        for node in nodes:
            station_name = node.get("NomeEstacao")
            scheduled_time_str = node.get("HoraProgramada")
            obs = node.get("Observacoes", "") or ""
            passed = node.get("ComboioPassou", False)

            if not passed:
                all_passed = False

            # Parse scheduled time and handle day wrap
            if scheduled_time_str and current_sched_date:
                t = datetime.strptime(scheduled_time_str, "%H:%M")
                if last_sched_naive and t < last_sched_naive:
                    current_sched_date += timedelta(days=1)
                last_sched_naive = t
                sched_iso = self._to_utc_iso(scheduled_time_str, current_sched_date)
            else:
                sched_iso = scheduled_time_str

            # Determine estimated time and handle day wrap
            estim_iso = sched_iso
            if not passed:
                # Check for specific station observation first
                match = re.search(r"Hora Prevista\s*:\s*(\d{2}:\d{2})", obs)
                if match:
                    estim_time_str = match.group(1)
                elif delay > 0:
                    try:
                        t = datetime.strptime(scheduled_time_str, "%H:%M")
                        new_t = t + timedelta(minutes=delay)
                        estim_time_str = new_t.strftime("%H:%M")
                    except (ValueError, TypeError):
                        estim_time_str = scheduled_time_str
                else:
                    estim_time_str = scheduled_time_str

                if estim_time_str and current_estim_date:
                    t = datetime.strptime(estim_time_str, "%H:%M")
                    if last_estim_naive and t < last_estim_naive:
                        current_estim_date += timedelta(days=1)
                    last_estim_naive = t
                    estim_iso = self._to_utc_iso(estim_time_str, current_estim_date)

            stations.append({
                "name": station_name,
                "scheduled": sched_iso,
                "estimated": estim_iso,
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
