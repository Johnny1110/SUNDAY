"""Engine loop tests over in-memory fakes.

Proves the engine runs fully decoupled from ccxt/postgres/webhook: the SAME
Engine that trades live here drives reconcile/halt/tick against fakes. The Gate-2
backtest is this exact seam with a richer Broker (a fill model) + a replay Market
— so these tests are the evidence that the decoupling actually holds, runnable
with the stdlib alone (no deps, no DB).
"""

import unittest
from datetime import datetime, timezone

from sunday import risk
from sunday.engine import Engine, EngineConfig
from sunday.market import Candles


def candles(closes):
    n = len(closes)
    return Candles([i * 3_600_000 for i in range(n)], [closes[0]] + closes[:-1],
                   [c + 0.5 for c in closes], [c - 0.5 for c in closes],
                   list(map(float, closes)), [1.0] * n)


UP = candles([float(i) for i in range(1, 120)])   # strong uptrend → momentum votes long, regime trending
FLATTAPE = candles([100.0] * 120)                  # no movement → neutral vote, regime ranging


class FakeMarket:
    def __init__(self, tape):
        self.tape = tape
    def ohlcv(self, symbol, tf, limit): return self.tape
    def ticker(self, symbol): return self.tape.closes[-1]
    def funding_rate(self, symbol): return None


class FakeBroker:
    def __init__(self, equity=10_000.0):
        self._equity = equity
        self.pos = {}              # symbol -> (side, qty, entry)
        self.orders = []; self.stops = []; self.closed = []; self.canceled = []; self.lev = {}
    def current_side(self, symbol):
        p = self.pos.get(symbol)
        return p[0] if p else None
    def exposure_usd(self):
        return sum(qty * entry for _, qty, entry in self.pos.values())
    def equity(self): return self._equity
    def unrealized_total(self): return 0.0
    def capture_realized(self, symbol): return 0.0
    def place_market(self, symbol, side, qty):
        self.orders.append((symbol, side, qty))
        self.pos[symbol] = ("long" if side == "buy" else "short", qty, 100.0)
        return {"id": len(self.orders), "status": "filled"}
    def set_leverage(self, symbol, lev): self.lev[symbol] = lev
    def set_stop(self, symbol, close_side, qty, stop_price): self.stops.append((symbol, stop_price))
    def close(self, symbol): self.closed.append(symbol); self.pos.pop(symbol, None)
    def cancel_stops(self, symbol): self.canceled.append(symbol)


class FakeLedger:
    def __init__(self, strategy="momentum", mode="active", peak=None, hb_age=None,
                 last_regime=None, envelope=None):
        self._strategy, self._mode, self._peak = strategy, mode, peak
        self._hb, self._regime, self._envelope = hb_age, last_regime, envelope
        self.signals = []; self.orders = []; self.positions = []
        self.risk_events = []; self.pnl = []; self.strategy_sets = []
        self.envelope_sets = []; self.rationale = None
    def current_strategy(self, symbol): return self._strategy
    def set_strategy(self, symbol, strategy, reason, set_by):
        self._strategy = strategy; self.strategy_sets.append((strategy, reason, set_by))
    def record_signal(self, symbol, strategy, indicators, action): self.signals.append((strategy, action))
    def set_rationale(self, text): self.rationale = text
    def get_mode(self): return self._mode
    def set_mode(self, mode): self._mode = mode
    def close_open_positions(self, symbol, realized_pnl=None): self.positions.append(("close", symbol, realized_pnl))
    def record_order(self, *a): self.orders.append(a)
    def record_position_open(self, *a): self.positions.append(("open",) + a)
    def record_risk_event(self, type_, detail, action_taken): self.risk_events.append((type_, action_taken))
    def get_last_regime(self): return self._regime
    def set_last_regime(self, regime): self._regime = regime
    def heartbeat_age(self): return self._hb
    def realized_total(self): return 0.0
    def equity_peak(self): return self._peak
    def record_pnl_snapshot(self, equity, realized, unrealized, drawdown_pct): self.pnl.append((equity, drawdown_pct))
    def get_envelope(self): return self._envelope
    def set_envelope(self, env, reason, set_by): self._envelope = env; self.envelope_sets.append((env, reason, set_by))


class FakeSink:
    def __init__(self): self.events = []
    def emit(self, event): self.events.append(event); return {"ok": True}


class FakeClock:
    def now(self): return datetime(2026, 6, 8, tzinfo=timezone.utc)


def make(market=None, broker=None, ledger=None, sink=None, cfg=None):
    return Engine(market or FakeMarket(UP), broker or FakeBroker(), ledger or FakeLedger(),
                  sink or FakeSink(), FakeClock(), cfg or EngineConfig())


def kinds(sink):
    return [ev["data"]["event_type"] for ev in sink.events]


class TestReconcile(unittest.TestCase):
    def test_opens_long_from_flat_on_momentum_uptrend(self):
        b, led = FakeBroker(), FakeLedger(strategy="momentum")
        out = make(broker=b, ledger=led).reconcile("BTCUSDT")
        self.assertEqual(out["action"], "opened_long")
        self.assertEqual(b.current_side("BTCUSDT"), "long")
        self.assertTrue(b.stops)                       # protective stop set on entry
        self.assertTrue(led.orders and led.positions)  # ledger recorded the order + open

    def test_hold_when_already_aligned(self):
        b = FakeBroker(); b.pos["BTCUSDT"] = ("long", 1.0, 100.0)
        out = make(broker=b, ledger=FakeLedger(strategy="momentum")).reconcile("BTCUSDT")
        self.assertEqual(out["action"], "noop")

    def test_flat_strategy_closes_book(self):
        b = FakeBroker(); b.pos["BTCUSDT"] = ("long", 1.0, 100.0)
        out = make(broker=b, ledger=FakeLedger(strategy="flat")).reconcile("BTCUSDT")
        self.assertEqual(out["action"], "flat")
        self.assertIn("BTCUSDT", b.closed)

    def test_safe_mode_freezes_new_entries(self):
        out = make(ledger=FakeLedger(strategy="momentum", mode="safe")).reconcile("BTCUSDT")
        self.assertEqual(out["action"], "frozen_no_entry")

    def test_over_envelope_order_is_rejected_and_logged(self):
        tiny = EngineConfig(envelope=risk.Envelope(max_position_usd=10.0))  # 500 notional >> 10
        led = FakeLedger(strategy="momentum")
        with self.assertRaises(risk.RiskRejected):
            make(ledger=led, cfg=tiny).reconcile("BTCUSDT")
        self.assertEqual(led.risk_events[0][1], "order_rejected")

    def test_ledger_envelope_overrides_config_default(self):
        # the leader's /envelope lever (stored in the ledger) overrides the permissive cfg
        # default, so an order the default would allow is rejected by the tighter caps.
        led = FakeLedger(strategy="momentum", envelope=risk.Envelope(max_position_usd=10.0))
        with self.assertRaises(risk.RiskRejected):
            make(ledger=led).reconcile("BTCUSDT")              # make() uses default EngineConfig (2000 cap)
        self.assertEqual(led.risk_events[0][1], "order_rejected")


class TestHalt(unittest.TestCase):
    def test_halt_flat_closes_and_locks(self):
        b = FakeBroker(); b.pos["BTCUSDT"] = ("long", 1.0, 100.0); led = FakeLedger()
        out = make(broker=b, ledger=led).halt("flat", "test")
        self.assertEqual(out["mode"], "halted")
        self.assertIn("BTCUSDT", b.closed)
        self.assertEqual(led._strategy, "flat")

    def test_halt_safe_sets_mode(self):
        self.assertEqual(make(ledger=FakeLedger()).halt("safe", "x")["mode"], "safe")


class TestTick(unittest.TestCase):
    def test_regime_shift_emits_self_sufficient_event(self):
        sink, led = FakeSink(), FakeLedger(last_regime="ranging")  # tape UP → trending → shift
        make(market=FakeMarket(UP), ledger=led, sink=sink).tick()
        self.assertIn("regime_shift", kinds(sink))
        self.assertEqual(led._regime, "trending")

    def test_drawdown_breach_flattens_and_alerts(self):
        b = FakeBroker(equity=9_000.0)                 # 10% below peak 10k → breach (>5%)
        led = FakeLedger(mode="active", peak=10_000.0, last_regime="ranging")
        sink = FakeSink()
        out = make(market=FakeMarket(FLATTAPE), broker=b, ledger=led, sink=sink).tick()
        self.assertTrue(out["pnl_snapshot"]["breached"])
        self.assertEqual(led._mode, "halted")          # circuit breaker flattened + locked
        self.assertIn("risk_breach", kinds(sink))

    def test_quiet_tape_no_breach_no_events(self):
        led = FakeLedger(mode="active", peak=10_000.0, last_regime="ranging")  # equity==peak, regime steady
        sink = FakeSink()
        out = make(market=FakeMarket(FLATTAPE), broker=FakeBroker(10_000.0), ledger=led, sink=sink).tick()
        self.assertFalse(out["pnl_snapshot"]["breached"])
        self.assertEqual(sink.events, [])              # idle → no token-burning noise

    def test_watchdog_enters_safe_mode_on_stale_heartbeat(self):
        led = FakeLedger(mode="active", peak=10_000.0, hb_age=9_999, last_regime="ranging")
        cfg = EngineConfig(heartbeat_timeout_sec=5_400)
        out = make(market=FakeMarket(FLATTAPE), ledger=led, sink=FakeSink(), cfg=cfg).tick()
        self.assertTrue(out["watchdog"]["safe_mode"])
        self.assertEqual(led._mode, "safe")


if __name__ == "__main__":
    unittest.main()
