"""Optional, app-free arrival detection: ping the phone's IP on the home Wi-Fi.

The phone is considered "away" once it has not answered for
PRESENCE_AWAY_MINUTES. The next successful ping after that counts as an arrival.

Caveat: phones in deep sleep sometimes stop answering pings while still at home,
so keep PRESENCE_AWAY_MINUTES generous (15-30 min). Give the phone a DHCP
reservation on your router so its IP never changes, and on Android set the
home network's privacy option to "Use device MAC" (or keep the per-network
randomised MAC, which is stable for a given SSID).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from .config import settings

log = logging.getLogger(__name__)


async def _ping(ip: str) -> bool:
    for _ in range(3):
        proc = await asyncio.create_subprocess_exec(
            "ping", "-c", "1", "-W", "2", ip,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        if await proc.wait() == 0:
            return True
        await asyncio.sleep(1)
    return False


async def watch(on_arrival: Callable[[], Awaitable[None]]) -> None:
    ip = settings.presence_phone_ip
    away_after = settings.presence_away_minutes * 60
    log.info("Presence watcher started for %s (away after %d min)", ip, settings.presence_away_minutes)

    # Assume home at startup so a reboot doesn't trigger an announcement.
    last_seen = time.monotonic()
    is_home = True

    while True:
        try:
            if await _ping(ip):
                if not is_home:
                    log.info("Phone %s is back - arrival detected", ip)
                    is_home = True
                    asyncio.create_task(on_arrival())
                last_seen = time.monotonic()
            elif is_home and time.monotonic() - last_seen > away_after:
                log.info("Phone %s unseen for %d min - marking away", ip, settings.presence_away_minutes)
                is_home = False
        except Exception:  # noqa: BLE001 - never let the watcher die
            log.exception("Presence check failed")
        await asyncio.sleep(settings.presence_interval_seconds)
