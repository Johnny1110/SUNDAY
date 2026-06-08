-- Sunday engine — risk envelope lever (G2.1). The leader's POST /envelope writes a
-- row here; the engine reads the latest as its hard caps. Append-only = an audit
-- trail of who/when/why changed the caps (like strategy_state for strategies).

CREATE TABLE IF NOT EXISTS risk_envelope (
    id                     BIGSERIAL   PRIMARY KEY,
    max_position_usd       NUMERIC     NOT NULL,
    max_total_exposure_usd NUMERIC     NOT NULL,
    max_leverage           NUMERIC     NOT NULL,
    max_drawdown_pct       NUMERIC     NOT NULL,
    stop_pct               NUMERIC     NOT NULL,
    reason                 TEXT,                       -- leader's rationale (User-visible)
    set_by                 TEXT        NOT NULL,       -- agent name | 'system'
    set_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_risk_envelope_set_at ON risk_envelope (set_at DESC);
