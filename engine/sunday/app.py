"""Sunday HTTP service — an agent-native Binance USDⓈ-M proxy (milestone-6).

Thin FastAPI layer. Each API group's routes live under ``routers/`` (req 10 —
prefixes by module), exchange access in ``exchange.py`` (mainnet data + testnet
trade), local state in ``store.py`` (sqlite). This file only wires:

  * the routers (markets / klines / funding / perp / account / indices / alerts / monitor /
    journal / memory / reports / admin),
  * the system routes (``/health`` ``/manual`` ``/`` ``/dashboard``), and
  * the realtime background hub (price websockets → position monitor + alert engine).

Everything is token-free (req 9): Sunday holds the Binance keys; the agent holds only HTTP.
"""

from __future__ import annotations

import logging
import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import runtime, store
from .config import settings
from .routers import (account, admin, alerts, funding, indices, journal, klines, markets,
                      memory, monitor, perp, reports, system)

log = logging.getLogger("sunday")
_HERE = pathlib.Path(__file__).resolve().parent
_MANUAL = _HERE / "manual.md"
_WEB = _HERE / "web" / "dist"          # the built Vite app (req 7); served at / and /ui
_INDEX = _WEB / "index.html"


def _restore_monitor_config() -> None:
    """Re-apply monitor knobs persisted via POST /api/monitor/config across restarts."""
    enabled = store.kv_get("monitor_enabled")
    if enabled is not None:
        settings.monitor_enabled = enabled == "1"
    step = store.kv_get("monitor_step_pct")
    if step is not None:
        try:
            settings.monitor_step_pct = float(step)
        except ValueError:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.connect(settings.sqlite_path)
    _restore_monitor_config()
    log.info("sqlite ready at %s", settings.sqlite_path)
    from .pricehub import Realtime  # lazy: keeps app import light + test-friendly
    runtime.realtime = Realtime()
    await runtime.realtime.start()
    yield
    if runtime.realtime is not None:
        await runtime.realtime.stop()
        runtime.realtime = None
    store.close()


app = FastAPI(title="Sunday", version="0.6.0", lifespan=lifespan)

for _r in (markets.router, klines.router, funding.router, perp.router,
           account.router, indices.router, alerts.router, monitor.router, journal.router,
           memory.router, reports.router, system.router, admin.router):
    app.include_router(_r)

# Serve the built dashboard's assets (Vite outputs to web/dist with base=/ui/).
if _WEB.is_dir():
    app.mount("/ui", StaticFiles(directory=str(_WEB)), name="ui")


@app.get("/health")
def health() -> dict:
    """Liveness — does NOT touch the exchange (keep it a cheap, dependency-free ping)."""
    return {"ok": True, "service": "sunday", "version": app.version}


@app.get("/manual", response_class=PlainTextResponse)
def manual() -> str:
    """The agent-facing API manual (the proxy's contract)."""
    return _MANUAL.read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> str:
    """Sunday-served dashboard (the built Vite app). Placeholder until `npm run build`."""
    if _INDEX.is_file():
        return _INDEX.read_text(encoding="utf-8")
    return ("<!doctype html><meta charset=utf-8><title>Sunday</title>"
            "<body style='font-family:system-ui;background:#0b0e14;color:#cdd6f4;padding:3rem'>"
            "<h1>☀ Sunday</h1><p>The dashboard isn't built yet. Run "
            "<code>npm install &amp;&amp; npm run build</code> in <code>engine/sunday/web/</code>.</p>"
            "<p>The API is live regardless — see <a style='color:#89b4fa' href='/manual'>/manual</a>.</p>")
