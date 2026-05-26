"""Self-poll keepalive task.

Render's free-tier web services spin down after ~15 minutes of inactivity,
which adds a ~50 second cold-start to the next real request. To keep the
instance warm we periodically GET our own ``/healthz`` endpoint from inside
the app.

Behaviour:

* Interval is ``settings.self_ping_interval_seconds`` (default 600 s).
* Target URL is ``settings.self_ping_url`` if set, otherwise the
  ``RENDER_EXTERNAL_URL`` env var that Render injects automatically.
* If neither resolves to a usable URL, the task is skipped (useful for
  local development).
* The task is fully async, cancellation-safe, and lives for the lifespan
  of the FastAPI app.
* Self-pings are logged at DEBUG so they don't drown out real traffic.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


def _resolve_ping_url(settings: Settings) -> Optional[str]:
    """Return the URL to self-ping, or ``None`` if keepalive is disabled."""
    explicit = (settings.self_ping_url or "").strip()
    base = explicit or os.environ.get("RENDER_EXTERNAL_URL", "").strip()
    if not base:
        return None
    base = base.rstrip("/")
    path = settings.self_ping_path or "/healthz"
    if not path.startswith("/"):
        path = "/" + path
    return f"{base}{path}"


async def _keepalive_loop(url: str, interval_seconds: int) -> None:
    """Issue a GET against ``url`` every ``interval_seconds`` seconds."""
    logger.info(
        "Keepalive task started — pinging %s every %ds", url, interval_seconds
    )
    # Use a single client to reuse the underlying connection.
    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            try:
                await asyncio.sleep(interval_seconds)
                response = await client.get(url)
                logger.debug(
                    "Keepalive ping %s -> %s", url, response.status_code
                )
            except asyncio.CancelledError:
                logger.info("Keepalive task cancelled")
                raise
            except Exception as exc:  # noqa: BLE001 — never let this kill the app
                logger.warning("Keepalive ping failed: %s", exc)


def start_keepalive(settings: Settings) -> Optional[asyncio.Task[None]]:
    """Start the keepalive task and return its handle (or ``None`` if disabled)."""
    url = _resolve_ping_url(settings)
    if not url:
        logger.info(
            "Keepalive disabled — set SELF_PING_URL or run on Render "
            "(RENDER_EXTERNAL_URL will be auto-set)."
        )
        return None
    interval = max(30, int(settings.self_ping_interval_seconds))
    return asyncio.create_task(
        _keepalive_loop(url, interval), name="self-keepalive"
    )


async def stop_keepalive(task: Optional[asyncio.Task[None]]) -> None:
    """Cancel and await the keepalive task during shutdown."""
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:  # noqa: BLE001
        logger.exception("Keepalive task raised on shutdown")
