-- milestone-1.1 — runtime risk envelope (the leader's /envelope lever) + analyst
-- commentary (the consulting role's one harmless, User-facing write).

-- Single source of truth for the active risk envelope: latest row wins. The
-- engine loads the newest on startup (defaults when empty) so a leader's envelope
-- change survives a restart. Append-only = an audit trail of who tightened/loosened
-- the box and why (User-visible, like strategy_state.reason).
CREATE TABLE IF NOT EXISTS risk_envelope (
    id                      BIGSERIAL   PRIMARY KEY,
    max_position_usd        NUMERIC     NOT NULL,
    max_total_exposure_usd  NUMERIC     NOT NULL,
    max_leverage            NUMERIC     NOT NULL,
    max_drawdown_pct        NUMERIC     NOT NULL,
    stop_pct                NUMERIC     NOT NULL,
    reason                  TEXT,
    set_by                  TEXT        NOT NULL,
    set_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_envelope_set_at ON risk_envelope (set_at DESC);

-- analyst's market-context posts — curated feed for the User (PRD §7.11). Not a
-- trading lever; harmless write.
CREATE TABLE IF NOT EXISTS commentary (
    id      BIGSERIAL   PRIMARY KEY,
    ts      TIMESTAMPTZ NOT NULL DEFAULT now(),
    author  TEXT        NOT NULL,
    body    TEXT        NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_commentary_ts ON commentary (ts DESC);
