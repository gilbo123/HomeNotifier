"""Fetch BYD vehicle telemetry (battery percentage)."""

from __future__ import annotations

import asyncio
import logging
import os

from .config import settings

log = logging.getLogger(__name__)


async def get_battery_percentage() -> float | None:
    """Fetch the battery percentage for the BYD vehicle.

    Returns None if pybyd is not installed, credentials are not set,
    or the request fails.
    """
    try:
        from pybyd import BydClient, BydConfig
    except ImportError:
        log.warning("pybyd is not installed; run: pip install pybyd")
        return None

    if not os.environ.get("BYD_USERNAME") or not os.environ.get("BYD_PASSWORD"):
        log.info("BYD credentials not configured; skipping battery check")
        return None

    try:
        if "BYD_COUNTRY_CODE" not in os.environ and settings.byd_country_code:
            os.environ["BYD_COUNTRY_CODE"] = settings.byd_country_code
        if "BYD_BASE_URL" not in os.environ and settings.byd_base_url:
            os.environ["BYD_BASE_URL"] = settings.byd_base_url

        config = BydConfig.from_env()
        async with BydClient(config) as client:
            vin = settings.byd_vin
            if not vin:
                vehicles = await asyncio.wait_for(client.get_vehicles(), timeout=15)
                if not vehicles:
                    log.warning("No BYD vehicles found on account")
                    return None
                vin = vehicles[0].vin

            realtime = await asyncio.wait_for(client.get_vehicle_realtime(vin), timeout=25)
            if realtime.elec_percent is not None:
                percent = float(realtime.elec_percent)
                log.info("BYD battery percentage: %s%% (VIN: %s)", percent, vin)
                return percent

            log.warning("BYD realtime data did not contain elec_percent")
            return None
    except Exception as exc:
        log.warning("Failed to fetch BYD battery (%s)", exc)
        return None
