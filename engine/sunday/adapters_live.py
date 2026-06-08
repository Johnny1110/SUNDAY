"""Live adapters — implement the ports against ccxt / postgres+redis / the webhook.

Thin translation only: ccxt position/balance dicts → the engine's domain shapes
(current_side / exposure_usd / equity …), store module functions → the Ledger
interface, urllib webhook → the EventSink. All the trading logic lives in
engine.py over the ports; this file is the live edge. The Gate-2 sim adapters
(replay market + simulated-fill broker + in-memory ledger) implement the same
ports and slot in unchanged.

Imports the heavy deps (ccxt via exchange, psycopg/redis via store) — so it is
NOT importable in a dep-free sandbox; it is syntax-checked + cross-checked there
and exercised live in the deploy environment.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import events, exchange, risk, store
from .market import Candles


class CcxtMarket:
    """MarketData over the ccxt USDⓈ-M adapter."""

    def ohlcv(self, symbol: str, tf: str, limit: int) -> Candles:
        return Candles.from_klines(exchange.fetch_ohlcv(symbol, tf, limit))

    def ticker(self, symbol: str) -> float:
        return float(exchange.fetch_ticker(symbol)["last"])

    def funding_rate(self, symbol: str) -> float | None:
        return exchange.fetch_funding_rate(symbol)


class CcxtBroker:
    """Broker over ccxt — translates position/balance rows into domain values."""

    def current_side(self, symbol: str) -> str | None:
        target = exchange._sym(symbol)
        for p in exchange.fetch_positions():
            if p["symbol"] == target and p.get("contracts"):
                return p["side"]
        return None

    def exposure_usd(self) -> float:
        total = 0.0
        for p in exchange.fetch_positions():
            if p.get("contracts"):
                total += abs(float(p["contracts"]) * float(p.get("markPrice") or p.get("entryPrice") or 0))
        return total

    def equity(self) -> float:
        bal = exchange.fetch_balance()
        return float((bal.get("total") or {}).get("USDT") or 0.0)

    def unrealized_total(self) -> float:
        return sum(float(p.get("unrealizedPnl") or 0) for p in exchange.fetch_positions())

    def capture_realized(self, symbol: str) -> float:
        target = exchange._sym(symbol)
        total = 0.0
        for p in exchange.fetch_positions():
            if p["symbol"] == target and p.get("contracts"):
                total += float(p.get("unrealizedPnl") or 0)
        return total

    def place_market(self, symbol: str, side: str, qty: float) -> dict:
        return exchange.place_market(symbol, side, qty)

    def set_leverage(self, symbol: str, leverage: int) -> None:
        exchange.set_leverage(symbol, leverage)

    def set_stop(self, symbol: str, close_side: str, qty: float, stop_price: float):
        return exchange.set_stop(symbol, close_side, qty, stop_price)

    def close(self, symbol: str):
        return exchange.close_position(symbol)

    def cancel_stops(self, symbol: str) -> None:
        exchange.cancel_all_orders(symbol)


class LiveLedger:
    """Ledger over the postgres/redis store module."""

    def current_strategy(self, symbol: str) -> str:
        return store.current_strategy(symbol)

    def set_strategy(self, symbol: str, strategy: str, reason: str, set_by: str) -> None:
        store.set_strategy(symbol, strategy, reason, set_by)

    def record_signal(self, symbol: str, strategy: str, indicators: dict, action: str) -> None:
        store.record_signal(symbol, strategy, indicators, action)

    def set_rationale(self, text: str) -> None:
        store.set_rationale(text)

    def get_mode(self) -> str:
        return store.get_mode()

    def set_mode(self, mode: str) -> None:
        store.set_mode(mode)

    def close_open_positions(self, symbol: str, realized_pnl: float | None = None) -> None:
        store.close_open_positions(symbol, realized_pnl=realized_pnl)

    def record_order(self, symbol, side, type_, qty, price, status, exchange_order_id, strategy, intent) -> None:
        store.record_order(symbol, side, type_, qty, price, status, exchange_order_id, strategy, intent)

    def record_position_open(self, symbol, side, qty, entry, stop, strategy, entry_reason) -> None:
        store.record_position_open(symbol, side, qty, entry, stop, strategy, entry_reason)

    def record_risk_event(self, type_: str, detail: dict, action_taken: str) -> None:
        store.record_risk_event(type_, detail, action_taken)

    def get_last_regime(self) -> str | None:
        return store.get_last_regime()

    def set_last_regime(self, regime: str) -> None:
        store.set_last_regime(regime)

    def heartbeat_age(self) -> float | None:
        return store.heartbeat_age()

    def realized_total(self) -> float:
        return store.realized_total()

    def equity_peak(self) -> float | None:
        return store.equity_peak()

    def record_pnl_snapshot(self, equity, realized, unrealized, drawdown_pct) -> None:
        store.record_pnl_snapshot(equity, realized, unrealized, drawdown_pct)

    def get_envelope(self) -> risk.Envelope | None:
        row = store.current_envelope()
        return risk.Envelope(**row) if row else None

    def set_envelope(self, env: risk.Envelope, reason: str | None, set_by: str) -> None:
        store.set_envelope(env.max_position_usd, env.max_total_exposure_usd, env.max_leverage,
                           env.max_drawdown_pct, env.stop_pct, reason, set_by)


class WallClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class WebhookSink:
    """EventSink: POST the event to the evva webhook + log it to webhook_log."""

    def __init__(self, url: str) -> None:
        self.url = url

    def emit(self, event: dict) -> dict:
        http_status, ok = events.post(self.url, event)
        data = event.get("data") or {}
        store.record_webhook(data.get("event_type") or "event", event.get("to") or "leader",
                             event.get("title"), event.get("body"), http_status, None)
        store.set_last_event_ts(datetime.now(timezone.utc).isoformat())
        return {"http_status": http_status, "ok": ok}
