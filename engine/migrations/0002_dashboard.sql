-- Sunday engine — milestone 2.0 (dashboard) schema additions.
-- commentary: analyst's User-facing market notes. Harmless write, NOT a trading
-- lever (auto-allow). pnl_snapshots / positions.realized_pnl already exist in
-- 0001 — 2.0 starts *writing* them (no schema change needed there).

CREATE TABLE IF NOT EXISTS commentary (
    id     BIGSERIAL   PRIMARY KEY,
    ts     TIMESTAMPTZ NOT NULL DEFAULT now(),
    author TEXT        NOT NULL,             -- 'analyst' (2.0); other roles in 2.1+
    title  TEXT,
    body   TEXT        NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_commentary_ts ON commentary (ts DESC);
