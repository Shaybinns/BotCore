"""
Database Layer - PostgreSQL Integration

7 tables:
  analysis_notes    — one row per magic_number (EA instance); full run JSON per sod/intraday
  market_data_cache — global synthesized market intelligence (morning brief)
  current_positions — live MT5 positions, fully replaced on each EA update
  trade_events      — append-only audit log of EA execution confirmations
  users             — chat interface user accounts and message history
  strategies        — named strategy prompts, selectable per EA instance
  account_snapshots — latest account metrics per magic_number (upsert)

  test_inputs — one row per AI run (all test fields in a single table)
"""

import psycopg2
import os
import json
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()


def get_db_connection():
    """Get PostgreSQL connection."""
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        raise ValueError("DATABASE_URL not set in environment")
    return psycopg2.connect(database_url)


def init_database():
    """
    Create all tables if they don't exist.
    Safe to call multiple times (uses CREATE TABLE IF NOT EXISTS).
    Called automatically by the /api/health endpoint on first boot.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Drop legacy note_type-based analysis_notes (data cleared on migrate).
    cursor.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'analysis_notes'
                  AND column_name = 'note_type'
            ) THEN
                DROP TABLE analysis_notes;
            END IF;
        END $$
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS analysis_notes (
            id                SERIAL PRIMARY KEY,
            magic_number      BIGINT       NOT NULL,
            symbol            VARCHAR(20)  NOT NULL,
            strategy_name     VARCHAR(100) NOT NULL DEFAULT '',
            sod_analysis      TEXT,
            intraday_analysis TEXT,
            created_at        TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_at        TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (magic_number, symbol, strategy_name)
        )
    """)

    # One row per EA instance — magic_number is the only unique identity.
    cursor.execute("""
        DELETE FROM analysis_notes a
        USING analysis_notes b
        WHERE a.magic_number = b.magic_number
          AND a.magic_number > 0
          AND a.id < b.id
    """)
    cursor.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'analysis_notes_magic_number_symbol_strategy_name_key'
            ) THEN
                ALTER TABLE analysis_notes
                    DROP CONSTRAINT analysis_notes_magic_number_symbol_strategy_name_key;
            END IF;
        END $$
    """)
    cursor.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'analysis_notes_magic_number_key'
            ) THEN
                ALTER TABLE analysis_notes
                    ADD CONSTRAINT analysis_notes_magic_number_key UNIQUE (magic_number);
            END IF;
        END $$
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_data_cache (
            id          SERIAL PRIMARY KEY,
            data        JSONB        NOT NULL,
            created_at  TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("DROP TABLE IF EXISTS bot_action CASCADE")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS current_positions (
            id            SERIAL PRIMARY KEY,
            magic_number  BIGINT          NOT NULL DEFAULT 0,
            symbol        VARCHAR(20)     NOT NULL,
            trade_id      BIGINT          NOT NULL,
            asset         VARCHAR(20)     NOT NULL,
            direction     VARCHAR(10)     NOT NULL,
            entry_price   DECIMAL(18, 5)  NOT NULL,
            current_price DECIMAL(18, 5),
            stop_loss     DECIMAL(18, 5),
            take_profit   DECIMAL(18, 5),
            lot_size      DECIMAL(10, 2)  NOT NULL,
            entry_time    TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(magic_number, symbol, trade_id)
        )
    """)
    cursor.execute("""
        ALTER TABLE current_positions
            ADD COLUMN IF NOT EXISTS magic_number BIGINT NOT NULL DEFAULT 0
    """)
    # Legacy installs: ticket → trade_id
    cursor.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'current_positions'
                  AND column_name = 'ticket'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'current_positions'
                  AND column_name = 'trade_id'
            ) THEN
                ALTER TABLE current_positions RENAME COLUMN ticket TO trade_id;
            END IF;
        END $$
    """)
    cursor.execute("""
        ALTER TABLE current_positions DROP CONSTRAINT IF EXISTS current_positions_magic_number_symbol_ticket_key
    """)
    cursor.execute("""
        DROP INDEX IF EXISTS idx_current_positions_magic_symbol_ticket
    """)
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_current_positions_magic_symbol_trade_id
            ON current_positions(magic_number, symbol, trade_id)
    """)
    cursor.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'current_positions_magic_number_symbol_trade_id_key'
            ) THEN
                ALTER TABLE current_positions
                    ADD CONSTRAINT current_positions_magic_number_symbol_trade_id_key
                    UNIQUE (magic_number, symbol, trade_id);
            END IF;
        END $$
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trade_events (
            id          SERIAL PRIMARY KEY,
            symbol      VARCHAR(20)  NOT NULL,
            event_type  VARCHAR(50)  NOT NULL,
            event_data  JSONB,
            created_at  TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id         UUID                     PRIMARY KEY DEFAULT gen_random_uuid(),
            email           VARCHAR(255)             NOT NULL UNIQUE,
            password        VARCHAR(255),
            full_name       VARCHAR(255),
            created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            recent_messages JSONB                    NOT NULL DEFAULT '[]'
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS strategies (
            id              SERIAL PRIMARY KEY,
            strategy_name   VARCHAR(100) NOT NULL UNIQUE,
            strategy_prompt TEXT         NOT NULL,
            uploaded_by     VARCHAR(255) NOT NULL,
            created_at      TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS account_snapshots (
            id                   SERIAL PRIMARY KEY,
            magic_number         BIGINT       NOT NULL DEFAULT 0,
            symbol               VARCHAR(20)  NOT NULL,
            strategy_name        VARCHAR(100) NOT NULL DEFAULT '',
            created_at           TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            account_size         DECIMAL(18, 2),
            realised_pnl         DECIMAL(18, 2),
            unrealised_pnl       DECIMAL(18, 2),
            today_realised_pnl   DECIMAL(18, 2),
            week_pnl             DECIMAL(18, 2),
            month_pnl            DECIMAL(18, 2)
        )
    """)
    cursor.execute("""
        ALTER TABLE account_snapshots
            ADD COLUMN IF NOT EXISTS magic_number BIGINT NOT NULL DEFAULT 0
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_account_snapshots_magic_symbol_strategy_created "
        "ON account_snapshots(magic_number, symbol, strategy_name, created_at DESC)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            name       VARCHAR(100) PRIMARY KEY,
            applied_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # One-time wipe of append-only snapshot history before magic_number-only storage.
    cursor.execute("""
        INSERT INTO schema_migrations (name)
        VALUES ('account_snapshots_wipe_legacy')
        ON CONFLICT (name) DO NOTHING
        RETURNING name
    """)
    if cursor.fetchone():
        cursor.execute("TRUNCATE account_snapshots RESTART IDENTITY")
        print("[db] account_snapshots: legacy rows wiped (one-time migration)")

    # One live row per magic_number — migrate off old composite UNIQUE if present.
    cursor.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'account_snapshots_magic_symbol_strategy_key'
            ) THEN
                ALTER TABLE account_snapshots
                    DROP CONSTRAINT account_snapshots_magic_symbol_strategy_key;
            END IF;
        END $$
    """)
    cursor.execute("""
        DELETE FROM account_snapshots a
        USING account_snapshots b
        WHERE a.magic_number = b.magic_number
          AND a.id < b.id
    """)
    cursor.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'account_snapshots_magic_number_key'
            ) THEN
                ALTER TABLE account_snapshots
                    ADD CONSTRAINT account_snapshots_magic_number_key
                    UNIQUE (magic_number);
            END IF;
        END $$
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS test_inputs (
            id                SERIAL PRIMARY KEY,
            magic_number      BIGINT       NOT NULL,
            symbol            VARCHAR(20)  NOT NULL,
            strategy_name     VARCHAR(100) NOT NULL DEFAULT '',
            run_type          VARCHAR(10)  NOT NULL,
            test_macro        JSONB,
            test_ohlc         JSONB,
            test_sod          TEXT,
            test_intraday     TEXT,
            test_chart        TEXT,
            test_output       JSONB,
            created_at        TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_test_inputs_magic_created "
        "ON test_inputs(magic_number, created_at DESC)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_test_inputs_symbol_strategy "
        "ON test_inputs(symbol, strategy_name)"
    )

    # Legacy: separate test_* tables replaced by test_inputs (columns hold the data).
    for legacy_test_table in (
        "test_macro",
        "test_ohlc",
        "test_chart",
        "test_sod",
        "test_intraday",
        "test_output",
    ):
        cursor.execute(f"DROP TABLE IF EXISTS {legacy_test_table} CASCADE")

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_analysis_notes_lookup "
        "ON analysis_notes(magic_number, symbol, strategy_name)"
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_current_positions_symbol ON current_positions(symbol)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trade_events_symbol ON trade_events(symbol)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_strategies_name ON strategies(strategy_name)")

    conn.commit()
    cursor.close()
    conn.close()
    print("[db] Tables initialised OK")


# =============================================================================
# ANALYSIS NOTES
# One row per magic_number (EA instance). symbol/strategy are metadata only.
# =============================================================================

def get_analysis_record(
    magic_number: int,
    symbol: str = '',
    strategy_name: str = '',
) -> Optional[Dict[str, Any]]:
    """Load sod_analysis and intraday_analysis for an EA instance (by magic_number)."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT symbol, strategy_name, sod_analysis, intraday_analysis, created_at, updated_at
            FROM analysis_notes
            WHERE magic_number = %s
        """, (magic_number,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if not row:
            return None

        return {
            "magic_number": magic_number,
            "symbol": row[0],
            "strategy_name": row[1] or '',
            "sod_analysis": row[2],
            "intraday_analysis": row[3],
            "_db_created_at": row[4].isoformat() if row[4] else None,
            "_db_updated_at": row[5].isoformat() if row[5] else None,
        }

    except Exception as e:
        print(f"[db] get_analysis_record error: {e}")
        return None


def save_sod_analysis(
    magic_number: int,
    symbol: str,
    strategy_name: str,
    sod_analysis: str,
) -> bool:
    """Upsert SOD analysis text; clears intraday_analysis for a fresh trading day."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO analysis_notes
                (magic_number, symbol, strategy_name, sod_analysis, intraday_analysis, updated_at)
            VALUES (%s, %s, %s, %s, NULL, CURRENT_TIMESTAMP)
            ON CONFLICT (magic_number) DO UPDATE SET
                symbol            = EXCLUDED.symbol,
                strategy_name     = EXCLUDED.strategy_name,
                sod_analysis      = EXCLUDED.sod_analysis,
                intraday_analysis = NULL,
                updated_at        = CURRENT_TIMESTAMP
        """, (magic_number, symbol, strategy_name or '', sod_analysis))
        conn.commit()
        cursor.close()
        conn.close()
        return True

    except Exception as e:
        print(f"[db] save_sod_analysis error: {e}")
        return False


def save_intraday_analysis(
    magic_number: int,
    symbol: str,
    strategy_name: str,
    intraday_analysis: str,
) -> bool:
    """Upsert intraday analysis text (creates row if missing)."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO analysis_notes
                (magic_number, symbol, strategy_name, intraday_analysis, updated_at)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (magic_number) DO UPDATE SET
                symbol            = EXCLUDED.symbol,
                strategy_name     = EXCLUDED.strategy_name,
                intraday_analysis = EXCLUDED.intraday_analysis,
                updated_at        = CURRENT_TIMESTAMP
        """, (magic_number, symbol, strategy_name or '', intraday_analysis))
        conn.commit()
        cursor.close()
        conn.close()
        return True

    except Exception as e:
        print(f"[db] save_intraday_analysis error: {e}")
        return False


def clear_intraday_analysis(
    magic_number: int,
    symbol: str,
    strategy_name: str = '',
) -> bool:
    """Clear intraday_analysis for an EA instance."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE analysis_notes
            SET intraday_analysis = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE magic_number = %s
        """, (magic_number,))
        conn.commit()
        cursor.close()
        conn.close()
        return True

    except Exception as e:
        print(f"[db] clear_intraday_analysis error: {e}")
        return False


# =============================================================================
# MARKET DATA CACHE (global)
# =============================================================================

def get_market_data_cache() -> Optional[Dict[str, Any]]:
    """Return latest cached market intelligence with _db_created_at injected."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT data, created_at
            FROM market_data_cache
            ORDER BY created_at DESC
            LIMIT 1
        """)
        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if not row:
            return None

        data = row[0] or {}
        if isinstance(data, str):
            data = json.loads(data)
        data["_db_created_at"] = row[1].isoformat() if row[1] else None
        return data

    except Exception as e:
        print(f"[db] get_market_data_cache error: {e}")
        return None


def save_market_data_cache(data: Dict[str, Any]) -> bool:
    """Append a new market intelligence snapshot (latest row wins on read)."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO market_data_cache (data, created_at)
            VALUES (%s, CURRENT_TIMESTAMP)
        """, (json.dumps(data),))
        conn.commit()
        cursor.close()
        conn.close()
        return True

    except Exception as e:
        print(f"[db] save_market_data_cache error: {e}")
        return False


# =============================================================================
# MAGIC NUMBER (EA instance identity)
# =============================================================================

def magic_number_is_available(
    magic_number: int,
    symbol: str = "",
    strategy_name: str = "",
) -> tuple[bool, str]:
    """
    magic_number is the globally unique EA instance id (must be > 0).
    Other magics may use the same symbol/strategy; the same magic_number cannot
    be claimed by a different symbol/strategy pair once registered in analysis_notes.
    Re-attaching the same chart (same magic + symbol + strategy) is allowed.
    """
    if magic_number <= 0:
        return False, (
            "magic_number must be a positive integer (> 0). "
            "Set a unique MagicNumber per EA chart in MT5 inputs."
        )

    sym = (symbol or "").strip().upper()
    strat = (strategy_name or "").strip()
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT symbol, strategy_name
            FROM analysis_notes
            WHERE magic_number = %s
        """, (magic_number,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if not row:
            return True, ""

        row_sym = (row[0] or "").strip().upper()
        row_strat = (row[1] or "").strip()
        if row_sym == sym and row_strat == strat:
            return True, ""

        return False, (
            f"Magic number {magic_number} is already registered on the server "
            f"({row_sym} / {row_strat}). Use a different MagicNumber for this chart."
        )

    except Exception as e:
        print(f"[db] magic_number_is_available error: {e}")
        return False, str(e)


# =============================================================================
# TEST INPUTS (append-only — one row per AI run, all fields in one table)
# =============================================================================

def _to_jsonb(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    if isinstance(value, str):
        try:
            json.loads(value)
            return value
        except json.JSONDecodeError:
            return json.dumps({"text": value})
    return json.dumps(value)


def _chart_to_text(chart: Any) -> Optional[str]:
    if chart is None:
        return None
    if isinstance(chart, str):
        return chart
    return json.dumps(chart)


def save_test_run(
    magic_number: int,
    run_type: str,
    symbol: str,
    strategy_name: str,
    macro: Any,
    ohlc: Any,
    chart: Any,
    system_prompt: str,
    flat_output: Dict[str, Any],
    raw_gpt_response: Optional[str] = None,
) -> bool:
    """
    Append one test_inputs row for a trading run (testing/audit).

    run_type: 'sod' | 'intraday' — system prompt goes in test_sod or test_intraday; the other is NULL.
    """
    run_type = (run_type or "").lower()
    if run_type not in ("sod", "intraday"):
        raise ValueError("run_type must be 'sod' or 'intraday'")

    test_sod = system_prompt if run_type == "sod" else None
    test_intraday = system_prompt if run_type == "intraday" else None
    test_output = {
        "flat": flat_output,
        "raw_gpt_response": raw_gpt_response,
    }

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO test_inputs
                (magic_number, symbol, strategy_name, run_type,
                 test_macro, test_ohlc, test_sod, test_intraday, test_chart, test_output)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s::jsonb)
        """, (
            magic_number,
            symbol,
            strategy_name or "",
            run_type,
            _to_jsonb(macro),
            _to_jsonb(ohlc),
            test_sod,
            test_intraday,
            _chart_to_text(chart),
            _to_jsonb(test_output),
        ))
        conn.commit()
        cursor.close()
        conn.close()
        print(f"[db] test_inputs saved ({run_type}, magic {magic_number})")
        return True

    except Exception as e:
        print(f"[db] save_test_run error: {e}")
        return False


# =============================================================================
# LEGACY COMPATIBILITY (brain.py / api_server.py until SOD rework is wired)
# magic_number defaults to 0 when callers do not pass it yet.
# =============================================================================

_LEGACY_MAGIC = 0


def get_analysis_note(symbol: str, note_type: str, strategy_name: str = '') -> Optional[Dict[str, Any]]:
    """
    Deprecated adapter — use get_analysis_record / get_market_data_cache instead.
    """
    if symbol == 'GLOBAL' and note_type == 'market_data_note':
        return get_market_data_cache()

    record = get_analysis_record(_LEGACY_MAGIC, symbol, strategy_name)
    if not record:
        return None

    if note_type == 'sod_note':
        text = record.get('sod_analysis')
        if not text:
            return None
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return {"sod_analysis": text, "_db_created_at": record.get("_db_updated_at")}

    if note_type == 'last_run_note':
        text = record.get('intraday_analysis')
        if not text:
            return None
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return {"intraday_analysis": text, "_db_created_at": record.get("_db_updated_at")}

    return None


def save_analysis_note(symbol: str, note_type: str, full_response: Dict[str, Any], strategy_name: str = '') -> bool:
    """
    Deprecated adapter — use save_sod_analysis / save_intraday_analysis / save_market_data_cache.
    """
    if symbol == 'GLOBAL' and note_type == 'market_data_note':
        return save_market_data_cache(full_response)

    if note_type == 'sod_note':
        sod_text = full_response.get('sod_analysis')
        if sod_text is None:
            sod_text = json.dumps(full_response)
        elif not isinstance(sod_text, str):
            sod_text = json.dumps(sod_text)
        return save_sod_analysis(_LEGACY_MAGIC, symbol, strategy_name, sod_text)

    if note_type == 'last_run_note':
        text = full_response if isinstance(full_response, str) else json.dumps(full_response)
        return save_intraday_analysis(_LEGACY_MAGIC, symbol, strategy_name, text)

    return False


def clear_analysis_notes(symbol: str, note_types: List[str], strategy_name: str = '') -> bool:
    """Deprecated adapter — clears intraday_analysis when last_run_note is listed."""
    if 'last_run_note' in note_types:
        return clear_intraday_analysis(_LEGACY_MAGIC, symbol, strategy_name)
    return True


# =============================================================================
# CURRENT POSITIONS
# Fully replaced on every EA update — always reflects live MT5 state.
# =============================================================================

def _entry_time_for_db(raw: Any) -> Any:
    """Accept ISO string or Unix seconds from EA payloads."""
    if raw is None:
        return datetime.now(timezone.utc)
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(int(raw), tz=timezone.utc)
    if isinstance(raw, str) and raw.isdigit():
        return datetime.fromtimestamp(int(raw), tz=timezone.utc)
    return raw


def normalize_positions_for_storage(
    positions: List[Dict[str, Any]],
    symbol: str,
) -> List[Dict[str, Any]]:
    """Map EA/legacy field names to DB rows (trade_id, direction, asset)."""
    out: List[Dict[str, Any]] = []
    for raw in positions or []:
        if not isinstance(raw, dict):
            continue
        trade_id = raw.get("trade_id") if raw.get("trade_id") is not None else raw.get("ticket")
        if trade_id is None:
            continue
        direction = (raw.get("direction") or raw.get("type") or "").strip().upper()
        if not direction:
            continue
        asset = (raw.get("asset") or symbol or "").strip()
        if not asset:
            continue
        out.append({
            "trade_id": int(trade_id),
            "asset": asset,
            "direction": direction,
            "entry_price": raw.get("entry_price"),
            "current_price": raw.get("current_price"),
            "stop_loss": raw.get("stop_loss"),
            "take_profit": raw.get("take_profit"),
            "lot_size": raw.get("lot_size"),
            "entry_time": _entry_time_for_db(raw.get("entry_time")),
        })
    return out


def store_current_positions(
    symbol: str,
    positions: List[Dict[str, Any]],
    magic_number: int,
) -> bool:
    """Replace all positions for an EA instance (magic_number + symbol)."""
    normalized = normalize_positions_for_storage(positions, symbol)
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            "DELETE FROM current_positions WHERE symbol = %s AND magic_number = %s",
            (symbol, magic_number),
        )

        for pos in normalized:
            cursor.execute("""
                INSERT INTO current_positions
                (magic_number, symbol, trade_id, asset, direction, entry_price, current_price,
                 stop_loss, take_profit, lot_size, entry_time, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            """, (
                magic_number,
                symbol,
                pos["trade_id"],
                pos["asset"],
                pos["direction"],
                pos.get("entry_price"),
                pos.get("current_price"),
                pos.get("stop_loss"),
                pos.get("take_profit"),
                pos.get("lot_size"),
                pos.get("entry_time"),
            ))

        conn.commit()
        cursor.close()
        conn.close()
        print(f"[db] Stored {len(normalized)} position(s) for {symbol}")
        return True

    except Exception as e:
        print(f"[db] store_current_positions error: {e}")
        return False


def get_current_positions(symbol: str, magic_number: int) -> List[Dict[str, Any]]:
    """Return live positions for an EA instance (magic_number + symbol)."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT trade_id, asset, direction, entry_price, current_price,
                   stop_loss, take_profit, lot_size, entry_time, updated_at
            FROM current_positions
            WHERE symbol = %s AND magic_number = %s
            ORDER BY entry_time ASC
        """, (symbol, magic_number))
        results = cursor.fetchall()
        cursor.close()
        conn.close()

        return [{
            "trade_id":      int(row[0]),
            "asset":         row[1],
            "direction":     row[2],
            "entry_price":   float(row[3]),
            "current_price": float(row[4]) if row[4] else None,
            "stop_loss":     float(row[5]) if row[5] else None,
            "take_profit":   float(row[6]) if row[6] else None,
            "lot_size":      float(row[7]),
            "entry_time":    row[8].isoformat() if row[8] else None,
        } for row in results]

    except Exception as e:
        print(f"[db] get_current_positions error: {e}")
        return []


# =============================================================================
# TRADE EVENTS
# Append-only audit log. Never deleted or overwritten.
# =============================================================================

def save_trade_event(symbol: str, event_type: str, event_data: Dict[str, Any]) -> bool:
    """Append a trade execution event to the audit log."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO trade_events (symbol, event_type, event_data)
            VALUES (%s, %s, %s)
        """, (symbol, event_type, json.dumps(event_data)))
        conn.commit()
        cursor.close()
        conn.close()
        return True

    except Exception as e:
        print(f"[db] save_trade_event error: {e}")
        return False


# =============================================================================
# STRATEGIES
# Named trading strategy prompts — selectable per EA instance.
# One row per strategy_name; upserted on save (last write wins).
# =============================================================================

def save_strategy(strategy_name: str, strategy_prompt: str, uploaded_by: str) -> bool:
    """
    Upsert a strategy. If strategy_name already exists, the prompt and
    uploaded_by are updated and updated_at is refreshed.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO strategies (strategy_name, strategy_prompt, uploaded_by, created_at, updated_at)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (strategy_name) DO UPDATE SET
                strategy_prompt = EXCLUDED.strategy_prompt,
                uploaded_by     = EXCLUDED.uploaded_by,
                updated_at      = CURRENT_TIMESTAMP
        """, (strategy_name.strip(), strategy_prompt.strip(), uploaded_by.strip()))
        conn.commit()
        cursor.close()
        conn.close()
        return True

    except Exception as e:
        print(f"[db] save_strategy error: {e}")
        return False


def get_strategy(strategy_name: str) -> Optional[Dict[str, Any]]:
    """Return a single strategy by name, or None if not found."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT strategy_name, strategy_prompt, uploaded_by, created_at, updated_at
            FROM strategies
            WHERE strategy_name = %s
        """, (strategy_name.strip(),))
        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if not row:
            return None

        return {
            "strategy_name":   row[0],
            "strategy_prompt": row[1],
            "uploaded_by":     row[2],
            "created_at":      row[3].isoformat() if row[3] else None,
            "updated_at":      row[4].isoformat() if row[4] else None,
        }

    except Exception as e:
        print(f"[db] get_strategy error: {e}")
        return None


def list_strategies() -> List[Dict[str, Any]]:
    """Return all strategies ordered by name (prompt text excluded for brevity)."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT strategy_name, uploaded_by, created_at, updated_at
            FROM strategies
            ORDER BY strategy_name ASC
        """)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        return [{
            "strategy_name": row[0],
            "uploaded_by":   row[1],
            "created_at":    row[2].isoformat() if row[2] else None,
            "updated_at":    row[3].isoformat() if row[3] else None,
        } for row in rows]

    except Exception as e:
        print(f"[db] list_strategies error: {e}")
        return []


def delete_strategy(strategy_name: str) -> bool:
    """Delete a strategy by name."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM strategies WHERE strategy_name = %s", (strategy_name.strip(),))
        deleted = cursor.rowcount > 0
        conn.commit()
        cursor.close()
        conn.close()
        return deleted

    except Exception as e:
        print(f"[db] delete_strategy error: {e}")
        return False


# =============================================================================
# ACCOUNT SNAPSHOTS
# One row per magic_number — overwritten each SOD/intraday (symbol/strategy stored for reference).
# =============================================================================

def save_account_snapshot(
    symbol: str,
    strategy_name: str,
    magic_number: int,
    account_size: Optional[float] = None,
    realised_pnl: Optional[float] = None,
    unrealised_pnl: Optional[float] = None,
    today_realised_pnl: Optional[float] = None,
    week_pnl: Optional[float] = None,
    month_pnl: Optional[float] = None,
) -> bool:
    """Upsert latest account metrics for this EA (keyed by magic_number only)."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO account_snapshots
            (magic_number, symbol, strategy_name, account_size, realised_pnl, unrealised_pnl,
             today_realised_pnl, week_pnl, month_pnl)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (magic_number)
            DO UPDATE SET
                symbol             = EXCLUDED.symbol,
                strategy_name      = EXCLUDED.strategy_name,
                account_size       = EXCLUDED.account_size,
                realised_pnl       = EXCLUDED.realised_pnl,
                unrealised_pnl     = EXCLUDED.unrealised_pnl,
                today_realised_pnl = EXCLUDED.today_realised_pnl,
                week_pnl           = EXCLUDED.week_pnl,
                month_pnl          = EXCLUDED.month_pnl,
                created_at         = CURRENT_TIMESTAMP
        """, (
            magic_number,
            symbol,
            strategy_name or '',
            account_size,
            realised_pnl,
            unrealised_pnl,
            today_realised_pnl,
            week_pnl,
            month_pnl,
        ))
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        print(f"[db] save_account_snapshot error: {e}")
        return False


def get_account_context_for_analysis(magic_number: int) -> Dict[str, Any]:
    """Return the account snapshot for this magic_number (one row per bot instance)."""
    result = {
        "magic_number": magic_number,
        "symbol": None,
        "strategy_name": None,
        "account_size": None,
        "realised_pnl": None,
        "unrealised_pnl": None,
        "today_realised_pnl": None,
        "week_pnl": None,
        "month_pnl": None,
        "snapshot_at": None,
    }
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT symbol, strategy_name, account_size, realised_pnl, unrealised_pnl,
                   today_realised_pnl, week_pnl, month_pnl, created_at
            FROM account_snapshots
            WHERE magic_number = %s
            LIMIT 1
        """, (magic_number,))
        row = cursor.fetchone()
        if row:
            result["symbol"] = row[0]
            result["strategy_name"] = row[1]
            result["account_size"] = float(row[2]) if row[2] is not None else None
            result["realised_pnl"] = float(row[3]) if row[3] is not None else None
            result["unrealised_pnl"] = float(row[4]) if row[4] is not None else None
            result["today_realised_pnl"] = float(row[5]) if row[5] is not None else None
            result["week_pnl"] = float(row[6]) if row[6] is not None else None
            result["month_pnl"] = float(row[7]) if row[7] is not None else None
            result["snapshot_at"] = row[8].isoformat() if row[8] else None

        cursor.close()
        conn.close()
    except Exception as e:
        print(f"[db] get_account_context_for_analysis error: {e}")
    return result
