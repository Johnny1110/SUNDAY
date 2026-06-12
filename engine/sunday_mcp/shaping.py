"""Pure shaping: engine JSON → compact, decision-ready tool text (S3/S7).

Everything here is stdlib-only and takes plain dicts from the engine's
documented responses (see GET /manual). The output budget discipline lives
here: callers cap page sizes in their schemas, and these renderers emit one
line per row — together that keeps every tool comfortably under the 60k-char
design budget (evva truncates tool results at 100k).

Rendering conventions (shared by every Phase 2 tool):
  * prices pass the engine's precision through (no rounding policy here)
  * percentages: 2 decimals, explicit sign
  * USD magnitudes: k / M / B suffix
  * missing value: "?"
"""

from __future__ import annotations

MEMO_MAX = 60  # chars of memo echoed per position row (full memo is in the UI)


# ── number rendering ──────────────────────────────────────────────────────────

def fmt_price(v: float | int | None) -> str:
    if v is None:
        return "?"
    s = f"{float(v):.10f}".rstrip("0").rstrip(".")
    return s or "0"


def fmt_pct(v: float | int | None) -> str:
    if v is None:
        return "?"
    return f"{float(v):+.2f}%"


def fmt_pct_plain(v: float | int | None) -> str:
    """Unsigned percentage — for magnitudes where a + sign would mislead
    (drawdown depth, exposure ratio)."""
    if v is None:
        return "?"
    return f"{float(v):.2f}%"


def fmt_frac_pct(v: float | int | None, signed: bool = False) -> str:
    """A FRACTION rendered as a percentage (funding rate 0.0001 → 0.01%,
    taker fee 0.0004 → 0.04%)."""
    if v is None:
        return "?"
    s = f"{float(v) * 100:+.4f}" if signed else f"{float(v) * 100:.4f}"
    s = s.rstrip("0").rstrip(".")
    return (s or "0") + "%"


def fmt_signed(v: float | int | None) -> str:
    """Signed USDT amount (PnL columns)."""
    if v is None:
        return "?"
    return f"{float(v):+.2f}"


def fmt_usd(v: float | int | None) -> str:
    if v is None:
        return "?"
    n = float(v)
    for cut, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(n) >= cut:
            return f"{n / cut:.2f}{suffix}"
    return fmt_price(n)


def clip(s: str | None, limit: int = MEMO_MAX) -> str | None:
    if s is None:
        return None
    s = " ".join(s.split())  # memos render on one row line
    return s if len(s) <= limit else s[: limit - 1] + "…"


def page_tail(payload: dict) -> str:
    has_more = "true" if payload.get("has_more") else "false"
    return (f"page {payload.get('page', '?')} · total {payload.get('total', '?')}"
            f" · has_more: {has_more}")


def stale_banner(payload: dict) -> str | None:
    """First-line warning when the engine served last-good data (PRD-005
    degrade). The number is still usable for judgement — just not live."""
    if not payload.get("stale"):
        return None
    age = payload.get("stale_age_s")
    return f"⚠ stale (age {age}s)" if age is not None else "⚠ stale"


# ── /api/markets ──────────────────────────────────────────────────────────────

def shape_markets(payload: dict) -> str:
    rows = payload.get("items") or []
    if not rows:
        return "no markets matched\n" + page_tail(payload)
    lines = [
        f"{r.get('symbol', '?')}  {fmt_price(r.get('last'))}"
        f"  {fmt_pct(r.get('change_pct'))}  {fmt_usd(r.get('quote_volume'))} vol"
        for r in rows
    ]
    return "\n".join(lines) + "\n" + page_tail(payload)


# ── /api/account/positions ────────────────────────────────────────────────────

def protection_str(prot: dict | None) -> str:
    """Render the engine's naked-position verdict. null ≠ "no legs" — it means
    the open-order books couldn't be read, and that uncertainty must survive
    into the agent's view (the engine's own semantics, see /manual)."""
    if prot is None:
        return "TP? SL?(unknown)"
    tp = "TP✓" if prot.get("take_profit") else "TP✗"
    if not prot.get("stop_loss"):
        sl = "SL✗(naked)"
    elif prot.get("sl_qty_covers") is False:
        sl = "SL△(partial)"
    else:
        sl = "SL✓"
    return f"{tp} {sl}"


def _position_line(r: dict) -> str:
    lev = r.get("leverage")
    line = (
        f"{r.get('symbol', '?')} {r.get('side', '?')} {fmt_price(r.get('qty'))}"
        f" @{fmt_price(r.get('entry'))} mark {fmt_price(r.get('mark'))}"
        f" roi {fmt_pct(r.get('roi_pct'))}"
        f" {lev if lev is not None else '?'}x {r.get('margin_mode') or '?'}"
        f" liq {fmt_pct(r.get('liq_distance_pct'))}"
        f" {protection_str(r.get('protection'))}"
    )
    memo = clip(r.get("memo"))
    return f"{line} | {memo}" if memo else line


def shape_positions(payload: dict) -> str:
    rows = payload.get("items") or []
    if not rows:
        return "no open positions"
    out = "\n".join(_position_line(r) for r in rows)
    if payload.get("has_more"):
        out += "\n" + page_tail(payload)
    return out


# ── /api/markets/{symbol} ─────────────────────────────────────────────────────

def shape_market_detail(payload: dict) -> str:
    t = payload.get("ticker") or {}
    info = payload.get("info") or {}
    prec = info.get("precision") or {}
    limits = info.get("limits") or {}
    amt = limits.get("amount") or {}
    cost = limits.get("cost") or {}
    # binanceusdm's 24h-ticker upstream never carries bid/ask — render the pair
    # only when present instead of a permanent "bid ? ask ?" (it reappears by
    # itself if the engine ever reads a book-ticker source)
    bid_ask = ""
    if t.get("bid") is not None or t.get("ask") is not None:
        bid_ask = f"  bid {fmt_price(t.get('bid'))}  ask {fmt_price(t.get('ask'))}"
    return "\n".join([
        f"{payload.get('symbol', '?')}  last {fmt_price(t.get('last'))}{bid_ask}"
        f"  24h {fmt_pct(t.get('change_pct'))}"
        f"  range {fmt_price(t.get('low'))}–{fmt_price(t.get('high'))}"
        f"  vol {fmt_usd(t.get('quote_volume'))}",
        f"precision: price {fmt_price(prec.get('price'))} · qty {fmt_price(prec.get('amount'))}"
        f" · contract_size {fmt_price(info.get('contract_size'))}",
        f"limits: qty {fmt_price(amt.get('min'))}–{fmt_price(amt.get('max'))}"
        f" · notional ≥ {fmt_price(cost.get('min'))}"
        f" · max leverage {info.get('max_leverage') or '?'}x",
        f"fees: maker {fmt_frac_pct(info.get('maker'))} · taker {fmt_frac_pct(info.get('taker'))}"
        f" · active: {str(info.get('active')).lower()}",
    ])


# ── /api/klines ───────────────────────────────────────────────────────────────

def shape_klines(payload: dict) -> str:
    lines = []
    banner = stale_banner(payload)
    if banner:
        lines.append(banner)
    lines.append(f"{payload.get('symbol', '?')} {payload.get('interval', '?')}"
                 f" · {payload.get('count', 0)} bars · ts,open,high,low,close,volume")
    for r in payload.get("ohlcv") or []:
        ts = r[0] if r else "?"
        lines.append(f"{ts}," + ",".join(fmt_price(v) for v in r[1:6]))
    return "\n".join(lines)


# ── /api/klines/indicators ────────────────────────────────────────────────────

def shape_indicators(payload: dict) -> str:
    lines = []
    banner = stale_banner(payload)
    if banner:
        lines.append(banner)
    lines.append(f"{payload.get('symbol', '?')} {payload.get('interval', '?')}"
                 f" · as_of {payload.get('as_of') or '?'}"
                 f" · last_close {fmt_price(payload.get('last_close'))}")
    panel = payload.get("indicators") or {}
    for name, val in panel.items():
        if isinstance(val, dict):
            lines.append(f"{name}: " + " · ".join(f"{k} {fmt_price(v)}" for k, v in val.items()))
        else:
            lines.append(f"{name} {fmt_price(val)}")
    if not panel:
        lines.append("no indicators computed (check `set`)")
    return "\n".join(lines)


# ── /api/funding (+/history) ──────────────────────────────────────────────────

def shape_funding(payload: dict) -> str:
    out = (f"{payload.get('symbol', '?')} funding {fmt_frac_pct(payload.get('rate'), signed=True)}"
           f" · mark {fmt_price(payload.get('mark'))} · index {fmt_price(payload.get('index'))}"
           f" · next_ts {payload.get('next_funding_ts') or '?'}")
    if payload.get("interval_hours"):
        out += f" · every {payload['interval_hours']}h"
    return out


def shape_funding_history(payload: dict) -> str:
    rows = payload.get("items") or []
    if not rows:
        return "no funding history\n" + page_tail(payload)
    lines = [f"{r.get('ts', '?')}  {fmt_frac_pct(r.get('rate'), signed=True)}" for r in rows]
    return "\n".join(lines) + "\n" + page_tail(payload)


# ── /api/indices ──────────────────────────────────────────────────────────────

def _index_line(it: dict) -> str:
    label = it.get("label") or it.get("key") or "?"
    if not it.get("available"):
        return f"{label}: unavailable"
    parts = [f"{label}: {fmt_price(it.get('value'))}{it.get('unit') or ''}"]
    if it.get("classification"):
        parts.append(str(it["classification"]))
    if it.get("change_pct") is not None:
        parts.append(fmt_pct(it.get("change_pct")))
    if it.get("stale"):
        parts.append("⚠ stale")
    return " · ".join(parts)


def shape_indices(payload: dict) -> str:
    items = payload.get("items") or []
    if not items:
        return "no indices available"
    return "\n".join(_index_line(it) for it in items)


def shape_index(payload: dict) -> str:
    return _index_line(payload) + f" · as_of {payload.get('as_of') or '?'}"


# ── /api/account (balance · pnl+drawdown · orders · trades) ───────────────────

def shape_balance(p: dict) -> str:
    return (f"equity {fmt_price(p.get('equity'))} · wallet {fmt_price(p.get('wallet'))}"
            f" · free {fmt_price(p.get('free'))} · used {fmt_price(p.get('used'))}"
            f" · unrealized {fmt_signed(p.get('unrealized_pnl'))}")


SHORT_HISTORY_SAMPLES = 50  # below this, the drawdown number rests on thin data


def shape_pnl_drawdown(pnl: dict | None, drawdown: dict | None,
                       pnl_error: str | None = None, dd_error: str | None = None) -> str:
    """Merged /pnl + /drawdown view. Either half may be missing (partial
    upstream failure) — the good half still renders, the bad half says why."""
    lines = []
    if pnl is not None:
        lines.append(f"equity {fmt_price(pnl.get('equity'))}"
                     f" · unrealized {fmt_signed(pnl.get('unrealized_pnl'))}"
                     f" · notional {fmt_usd(pnl.get('total_notional'))}"
                     f" · exposure {fmt_pct_plain(pnl.get('exposure_pct'))}")
        rows = pnl.get("positions") or []
        for r in rows:
            lines.append(f"  {r.get('symbol', '?')} {r.get('side', '?')}"
                         f" notional {fmt_usd(r.get('notional'))}"
                         f" upnl {fmt_signed(r.get('unrealized_pnl'))}"
                         f" roi {fmt_pct(r.get('roi_pct'))}")
        if not rows:
            lines.append("  no open positions")
    else:
        lines.append(f"pnl: {pnl_error or 'unavailable'}")
    if drawdown is not None:
        samples = drawdown.get("samples") or 0
        note = " (short history — low confidence)" if samples < SHORT_HISTORY_SAMPLES else ""
        lines.append(f"drawdown {fmt_pct_plain(drawdown.get('drawdown_pct'))}"
                     f" · high_water {fmt_price(drawdown.get('high_water'))}"
                     f" (ts {drawdown.get('high_water_ts') or '?'})"
                     f" · samples {samples}{note}")
    else:
        lines.append(f"drawdown: {dd_error or 'unavailable'}")
    return "\n".join(lines)


def _order_line(r: dict) -> str:
    if r.get("price"):
        px = f"@{fmt_price(r['price'])}"
    elif r.get("trigger_price"):
        px = f"trig {fmt_price(r['trigger_price'])}"
    else:
        px = "@?"
    flags = []
    if r.get("tp_sl") == "take_profit":
        flags.append("TP")
    if r.get("tp_sl") == "stop_loss":
        flags.append("SL")
    if r.get("algo"):
        flags.append("algo")
    if r.get("reduce_only"):
        flags.append("RO")
    if r.get("close_position"):
        flags.append("closepos")
    tag = f" [{' '.join(flags)}]" if flags else ""
    filled = r.get("filled") or 0
    fill = f" filled {fmt_price(filled)}" if filled else ""
    return (f"#{r.get('id', '?')} {r.get('symbol', '?')} {r.get('side', '?')}"
            f" {r.get('type', '?')} {px} qty {fmt_price(r.get('amount'))}{fill}"
            f" {r.get('status', '?')}{tag} · {r.get('agent') or 'agent:?'}")


def shape_orders(payload: dict) -> str:
    rows = payload.get("items") or []
    if not rows:
        return "no orders\n" + page_tail(payload)
    return "\n".join(_order_line(r) for r in rows) + "\n" + page_tail(payload)


def shape_trades(payload: dict) -> str:
    rows = payload.get("items") or []
    if not rows:
        return "no trades\n" + page_tail(payload)
    lines = [f"{r.get('ts', '?')} {r.get('side', '?')} {fmt_price(r.get('amount'))}"
             f" @{fmt_price(r.get('price'))} pnl {fmt_signed(r.get('realized_pnl'))}"
             f" fee {fmt_price(r.get('fee'))} · {r.get('agent') or 'agent:?'}"
             for r in rows]
    total = sum(r.get("realized_pnl") or 0.0 for r in rows)
    lines.append(f"Σ realized (this page): {total:+.2f}")
    return "\n".join(lines) + "\n" + page_tail(payload)


# ── GET /api/perp/protection ──────────────────────────────────────────────────

def shape_protection_status(payload: dict) -> str:
    pos = payload.get("position")
    tp, sl = payload.get("take_profit"), payload.get("stop_loss")
    tp_n, sl_n = payload.get("tp_legs") or 0, payload.get("sl_legs") or 0
    if pos is None and not (tp_n or sl_n):
        return f"{payload.get('symbol', '?')}: flat — no position, no trigger legs"

    lines = []
    if pos is None:
        lines.append("ORPHAN LEGS — no position behind these triggers; cancel them")
    else:
        lev = pos.get("leverage")
        lines.append(f"{payload.get('symbol', '?')} {pos.get('side', '?')}"
                     f" {fmt_price(pos.get('qty'))} @{fmt_price(pos.get('entry'))}"
                     f" mark {fmt_price(pos.get('mark'))} {lev if lev is not None else '?'}x")

    def leg_line(kind: str, leg: dict | None, n: int) -> str:
        if leg is None:
            return f"{kind}: none ({n} legs)"
        return (f"{kind} #{leg.get('id', '?')} trigger {fmt_price(leg.get('trigger_price'))}"
                f" {leg.get('status', '?')} ({n} leg{'s' if n != 1 else ''})")

    lines.append(leg_line("TP", tp, tp_n))
    sl_text = leg_line("SL", sl, sl_n)
    if payload.get("sl_qty_covers") is not None:
        sl_text += f" · covers qty: {str(payload['sl_qty_covers']).lower()}"
    lines.append(sl_text)
    return "\n".join(lines)


# ── write results (POST /api/perp/* · /api/alerts, PRD-9.3) ──────────────────
#
# Every write shaper ends with a one-line `next:` reminder — the SOP's
# "placing is not done" rule built into the tool feedback itself.

def _placed_bits(o: dict) -> str:
    """One placed order/leg from the engine's _norm_order shape:
    #id type @price|trig qty status [filled] [· algo]."""
    if o.get("price"):
        px = f"@{fmt_price(o['price'])}"
    elif o.get("trigger_price"):
        px = f"trig {fmt_price(o['trigger_price'])}"
    else:
        px = "@market"
    bits = (f"#{o.get('id', '?')} {o.get('type', '?')} {px}"
            f" qty {fmt_price(o.get('amount'))} {o.get('status', '?')}")
    filled = o.get("filled") or 0
    if filled and (o.get("status") or "").lower() != "filled":
        bits += f" filled {fmt_price(filled)}"  # partial fill on a resting order
    if o.get("algo"):
        bits += " · algo"
    return bits


def shape_order_result(p: dict) -> str:
    o = p.get("order") or {}
    lines = [f"placed: {o.get('symbol', '?')} {o.get('side', '?')} {_placed_bits(o)}"]
    applied = p.get("applied") or {}
    if applied:
        bits = []
        if applied.get("leverage"):
            bits.append(f"leverage {applied['leverage']}x")
        if applied.get("margin_mode"):
            bits.append(f"margin_mode {applied['margin_mode']}")
        if applied.get("margin_mode_note"):
            bits.append(f"⚠ {applied['margin_mode_note']}")
        lines.append("applied: " + " · ".join(bits))
    for kind, tag in (("take_profit", "TP"), ("stop_loss", "SL")):
        if p.get(kind):
            lines.append(f"{tag} leg {_placed_bits(p[kind])}")
    lines.append("next: verify with protection_status, then positions")
    return "\n".join(lines)


def shape_close_result(p: dict) -> str:
    c = p.get("closed") or {}
    lines = [f"closed: {c.get('symbol', '?')} {c.get('side', '?')} {_placed_bits(c)}"]
    if p.get("protection_sweep_error"):
        lines.append(f"⚠ protection sweep failed: {p['protection_sweep_error']}"
                     " — check protection_status for orphan legs")
    else:
        swept = p.get("cancelled_protection") or []
        lines.append("cancelled protection legs: "
                     + (", ".join(f"#{i}" for i in swept) if swept else "none"))
    if p.get("cancel_failed"):
        lines.append("⚠ cancel failed: " + ", ".join(f"#{i}" for i in p["cancel_failed"])
                     + " — cancel manually via cancel_order")
    lines.append("next: confirm flat via positions / protection_status")
    return "\n".join(lines)


def shape_protection_result(p: dict) -> str:
    lines = [f"{p.get('symbol', '?')} protection updated"]
    for kind, tag in (("take_profit", "TP"), ("stop_loss", "SL")):
        if p.get(kind):
            lines.append(f"{tag} leg {_placed_bits(p[kind])}")
    rep = p.get("replaced") or []
    lines.append("replaced old legs: "
                 + (", ".join(f"#{i}" for i in rep) if rep else "none"))
    if p.get("cancel_failed"):
        lines.append("⚠ old legs still resting: "
                     + ", ".join(f"#{i}" for i in p["cancel_failed"])
                     + " — reduce-only legs can't over-close; cancel via cancel_order")
    lines.append("next: verify with protection_status")
    return "\n".join(lines)


def shape_cancel_result(p: dict) -> str:
    return (f"cancelled #{p.get('cancelled', '?')}\n"
            "next: re-check open_orders (and protection_status if it was a TP/SL leg)")


def shape_cancel_all_result(p: dict) -> str:
    return (f"{p.get('symbol', '?')}: all resting orders cancelled\n"
            "next: open_orders should be empty — if a position remains it is now"
            " NAKED (TP/SL legs were resting orders too); re-attach via set_protection")


def shape_leverage_margin(margin: dict | None, leverage: dict | None,
                          margin_error: str | None = None,
                          leverage_error: str | None = None) -> str:
    """Two-segment result (margin-mode first, then leverage). Either segment can
    fail on its own — both outcomes always render, never hiding a half-success."""
    lines = []
    if margin is not None:
        lines.append(f"margin_mode: {margin.get('margin_mode', '?')}"
                     f" ({margin.get('result', 'set')})")
    elif margin_error:
        lines.append(f"margin_mode: {margin_error}")
    if leverage is not None:
        lines.append(f"leverage: {leverage.get('leverage', '?')}x set")
    elif leverage_error:
        lines.append(f"leverage: {leverage_error}")
    return "\n".join(lines)


# ── /api/alerts ───────────────────────────────────────────────────────────────

def _alert_line(a: dict) -> str:
    base = (f"#{a.get('id', '?')} {a.get('symbol', '?')} {a.get('kind', '?')}"
            f" {fmt_price(a.get('threshold'))} {a.get('status', '?')}")
    if a.get("kind") == "pct_move" and a.get("ref_price") is not None:
        base += f" (ref {fmt_price(a['ref_price'])})"
    if a.get("triggered_price") is not None:
        base += f" → fired @{fmt_price(a['triggered_price'])}"
    note = clip(a.get("note"))
    return f"{base} | {note}" if note else base


def shape_alert_created(a: dict) -> str:
    return _alert_line(a) + "\nfires once, then flips to status=triggered"


def shape_alerts_list(payload: dict) -> str:
    rows = payload.get("items") or []
    if not rows:
        return "no alerts\n" + page_tail(payload)
    return "\n".join(_alert_line(a) for a in rows) + "\n" + page_tail(payload)


def shape_alert_deleted(p: dict) -> str:
    return f"deleted alert #{p.get('deleted', '?')}"
