"""The engine loop — pure-ish orchestration over injected ports.

`Engine` holds the ports (market / broker / ledger / sink / clock) + an
`EngineConfig` and runs the supervision loop: read the tape → pure decision
(strategy.target_side, execution.plan_transition) → apply to the book under the
deterministic risk fuses (risk.check_order / check_drawdown). It imports only the
PURE core + ports + stdlib (NOT config/exchange/store), so the whole loop is
unit-testable with fakes — which is exactly what the Gate-2 backtest does: a sim
broker is just a richer Broker fake.

`live()` lazily wires the ccxt/postgres/webhook adapters; the module-level
`reconcile/halt/tick` delegate to it, so app.py needs no knowledge of the ports.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from . import events, execution, ports, regime, risk, strategy
from .market import Candles

log = logging.getLogger("sunday")


@dataclass
class EngineConfig:
    """The engine's tunables — a plain value object (NOT the pydantic settings), so
    the engine has no config/IO import and a backtest can pass its own."""
    symbol: str = "BTCUSDT"
    timeframe: str = "1h"
    candles_limit: int = 200          # bars pulled per decision (≥ slow + warmup)
    fast: int = 20
    slow: int = 50
    target_notional_usd: float = 500.0
    leverage: int = 3
    envelope: risk.Envelope = field(default_factory=risk.Envelope)
    webhook_url: str = ""
    heartbeat_timeout_sec: int = 5400


_ACTION_LABEL = {"long": "open_long", "short": "open_short", None: "go_flat"}


class Engine:
    def __init__(self, market: ports.MarketData, broker: ports.Broker, ledger: ports.Ledger,
                 sink: ports.EventSink, clock: ports.Clock, cfg: EngineConfig) -> None:
        self.market = market
        self.broker = broker
        self.ledger = ledger
        self.sink = sink
        self.clock = clock
        self.cfg = cfg

    # --- helpers ----------------------------------------------------------

    def _status_snapshot(self, symbol: str) -> dict:
        try:
            return {"mode": self.ledger.get_mode(), "strategy": self.ledger.current_strategy(symbol)}
        except Exception:
            return {}

    def _envelope(self) -> risk.Envelope:
        """Active risk caps: the leader's /envelope lever (via the ledger) overrides the config default."""
        return self.ledger.get_envelope() or self.cfg.envelope

    def notify(self, event: dict) -> object:
        """Fire a webhook event through the sink (post + log live; collect in sim)."""
        return self.sink.emit(event)

    # --- lever paths (called by app.py via the live singleton) ------------

    def reconcile(self, symbol: str, set_by: str = "system") -> dict:
        """Make the book match the active strategy's target (pure decision, live effects)."""
        strat = self.ledger.current_strategy(symbol)
        candles = self.market.ohlcv(symbol, self.cfg.timeframe, self.cfg.candles_limit)
        vote = strategy.evaluate(strat, candles, self.cfg.fast, self.cfg.slow)
        target = vote.vote if vote.vote in ("long", "short") else None
        self.ledger.record_signal(symbol, strat, vote.indicators, _ACTION_LABEL[target])
        self.ledger.set_rationale(vote.rationale)

        current = self.broker.current_side(symbol)
        action = execution.plan_transition(current, target)
        if action == execution.HOLD:
            return {"action": "noop", "side": current, "rationale": vote.rationale}

        if action in (execution.CLOSE, execution.FLIP_LONG, execution.FLIP_SHORT):
            realized = self.broker.capture_realized(symbol)
            self.broker.close(symbol)
            self.broker.cancel_stops(symbol)
            self.ledger.close_open_positions(symbol, realized_pnl=realized)

        if target is None:
            return {"action": "flat", "rationale": vote.rationale}

        mode = self.ledger.get_mode()
        if mode in ("safe", "halted"):                 # frozen: no new entries
            return {"action": "frozen_no_entry", "mode": mode, "rationale": vote.rationale}

        return self._open(symbol, target, strat, vote.rationale)

    def _open(self, symbol: str, side: str, strat: str, reason: str) -> dict:
        price = self.market.ticker(symbol)
        qty = round(self.cfg.target_notional_usd / price, 3)
        order = risk.OrderProposal(symbol, "buy" if side == "long" else "sell", qty, price,
                                   has_stop=True, is_entry=True)
        ctx = risk.RiskContext(equity=self.broker.equity(), current_exposure_usd=self.broker.exposure_usd())
        env = self._envelope()
        decision = risk.check_order(order, ctx, env)
        if not decision.allowed:                       # final line of defence — blocks any over-line order
            self.ledger.record_risk_event(decision.type or "rejected",
                                          {"symbol": symbol, "qty": qty, "price": price,
                                           "violations": decision.violations}, action_taken="order_rejected")
            raise risk.RiskRejected(f"{decision.type}: {decision.violations}")

        self.broker.set_leverage(symbol, self.cfg.leverage)
        od = self.broker.place_market(symbol, order.side, qty)
        self.ledger.record_order(symbol, order.side, "market", qty, price, od.get("status") or "new",
                                 str(od.get("id")), strat, reason)
        stop_px = risk.stop_price(side, price, env.stop_pct)
        self.broker.set_stop(symbol, "sell" if side == "long" else "buy", qty, stop_px)
        self.ledger.record_position_open(symbol, side, qty, price, stop_px, strat, reason)
        return {"action": f"opened_{side}", "qty": qty, "entry": price, "stop": stop_px, "rationale": reason}

    def halt(self, mode: str, reason: str) -> dict:
        """flat = close everything + cancel stops; safe = freeze new entries."""
        if mode == "flat":
            realized = self.broker.capture_realized(self.cfg.symbol)
            self.broker.close(self.cfg.symbol)
            self.broker.cancel_stops(self.cfg.symbol)
            self.ledger.close_open_positions(self.cfg.symbol, realized_pnl=realized)
            self.ledger.set_strategy(self.cfg.symbol, "flat", f"halt(flat): {reason}", "system")
            self.ledger.set_mode("halted")
        elif mode == "safe":
            self.ledger.set_mode("safe")
        return {"mode": self.ledger.get_mode()}

    # --- watcher tick -----------------------------------------------------

    def _detect_regime_and_notify(self, symbol: str) -> dict:
        try:
            candles = self.market.ohlcv(symbol, self.cfg.timeframe, self.cfg.candles_limit)
        except Exception as e:  # market/indicator failure → tell the leader
            self.notify(events.engine_degraded_event(f"無法取得行情/指標：{e}"))
            return {"degraded": str(e)}
        rr = regime.classify(candles)
        prev = self.ledger.get_last_regime()
        if regime.is_shift(prev, rr.label):
            self.ledger.set_last_regime(rr.label)
            self.notify(events.regime_shift_event(prev, rr, status=self._status_snapshot(symbol)))
            return {"regime": rr.label, "shifted": True, "prev": prev}
        if prev is None and rr.label != "unknown":
            self.ledger.set_last_regime(rr.label)  # baseline, no emit
        return {"regime": rr.label, "shifted": False}

    def _watchdog_check(self) -> dict:
        age = self.ledger.heartbeat_age()
        if age is not None and age > self.cfg.heartbeat_timeout_sec and self.ledger.get_mode() == "active":
            self.ledger.set_mode("safe")
            self.notify(events.safe_mode_event(
                f"swarm heartbeat 逾時 {int(age)}s（>{self.cfg.heartbeat_timeout_sec}s），凍結新倉",
                status=self._status_snapshot(self.cfg.symbol)))
            return {"safe_mode": True, "age": age}
        return {"safe_mode": False, "age": age}

    def _record_pnl_snapshot(self) -> dict:
        """One equity-curve point + the deterministic drawdown breaker (PRD §7.3)."""
        try:
            equity = self.broker.equity()
            unrealized = self.broker.unrealized_total()
        except Exception as e:
            return {"recorded": False, "error": str(e)}
        realized = self.ledger.realized_total()
        peak = self.ledger.equity_peak()
        peak = max(peak, equity) if peak is not None else equity
        env = self._envelope()
        dd = risk.check_drawdown(equity, peak, env)
        self.ledger.record_pnl_snapshot(equity, realized, unrealized, dd.drawdown_pct)
        if dd.breached and self.ledger.get_mode() == "active":  # flatten + lock, then alert
            self.halt("flat", f"drawdown {dd.drawdown_pct:.2f}% ≥ {env.max_drawdown_pct}%")
            self.ledger.record_risk_event("drawdown",
                                          {"equity": equity, "peak": peak, "drawdown_pct": dd.drawdown_pct},
                                          action_taken=dd.action or "flatten_and_lock")
            self.notify(events.risk_breach_event(
                f"drawdown {dd.drawdown_pct:.2f}% 觸發熔斷，已 flatten+lock",
                status=self._status_snapshot(self.cfg.symbol)))
        return {"recorded": True, "equity": equity, "drawdown_pct": dd.drawdown_pct, "breached": dd.breached}

    def tick(self) -> dict:
        """One periodic self-check (regime + watchdog + equity snapshot)."""
        sym = self.cfg.symbol
        return {
            "regime": self._detect_regime_and_notify(sym),
            "watchdog": self._watchdog_check(),
            "pnl_snapshot": self._record_pnl_snapshot(),
        }


# --- live wiring (lazy: only builds the heavy ccxt/pg/webhook adapters on demand) ---

_live: Engine | None = None


def _config_from_settings(settings) -> EngineConfig:
    return EngineConfig(
        symbol=settings.symbol,
        timeframe=settings.timeframe,
        candles_limit=settings.ema_slow + 50,
        fast=settings.ema_fast,
        slow=settings.ema_slow,
        target_notional_usd=settings.target_notional_usd,
        leverage=settings.leverage,
        envelope=risk.Envelope(
            max_position_usd=settings.max_position_usd,
            max_total_exposure_usd=settings.max_total_exposure_usd,
            max_leverage=settings.max_leverage,
            max_drawdown_pct=settings.max_drawdown_pct,
            stop_pct=settings.stop_pct,
        ),
        webhook_url=settings.evva_webhook_url,
        heartbeat_timeout_sec=settings.heartbeat_timeout_sec,
    )


def live() -> Engine:
    """The process-wide live engine (ccxt + postgres/redis + webhook adapters)."""
    global _live
    if _live is None:
        from .adapters_live import CcxtBroker, CcxtMarket, LiveLedger, WallClock, WebhookSink
        from .config import settings
        cfg = _config_from_settings(settings)
        _live = Engine(CcxtMarket(), CcxtBroker(), LiveLedger(), WebhookSink(cfg.webhook_url), WallClock(), cfg)
    return _live


def reconcile(symbol: str, set_by: str = "system") -> dict:
    return live().reconcile(symbol, set_by)


def halt(mode: str, reason: str) -> dict:
    return live().halt(mode, reason)


def tick() -> dict:
    return live().tick()
