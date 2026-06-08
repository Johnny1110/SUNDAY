-- milestone-3 (T3) — outcome attribution is a LENS over the existing
-- modeling-grade schema (M3-D3): no new fact tables, just an index that makes the
-- per-switch episode query (positions by symbol within a switch window) efficient.

CREATE INDEX IF NOT EXISTS idx_positions_symbol_opened ON positions (symbol, opened_at);
