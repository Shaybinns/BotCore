"""
Database Layer - PostgreSQL Integration

8 tables:
  analysis_notes    — per EA (magic_number + symbol + strategy): sod_analysis, intraday_analysis
  bot_action        — per EA (magic_number): next_review_time, action_type, enter/manage/exit payloads
  market_data_cache — global synthesized market intelligence (morning brief)
  current_positions — live MT5 positions, fully replaced on each EA update
  trade_events      — append-only audit log of EA execution confirmations
  users             — chat interface user accounts and message history
  strategies        — named strategy prompts, selectable per EA instance
  account_snapshots — per-run account metrics (balance, PnL) for AI context

  test_* tables (testing / audit — append-only per run):
  test_macro, test_ohlc, test_chart, test_sod, test_intraday, test_output
"""

import psycopg2
import os
import json
import uuid
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

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_data_cache (
            id          SERIAL PRIMARY KEY,
            data        JSONB        NOT NULL,
            created_at  TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bot_action (
            id                SERIAL PRIMARY KEY,
            magic_number      BIGINT       NOT NULL UNIQUE,
            next_review_time  VARCHAR(32),
            action_type       VARCHAR(10),
            enter             JSONB,
            manage            JSONB,
            exit_action       JSONB,
            created_at        TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_at        TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Migrate legacy bot_action columns if present.
    cursor.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'bot_action'
                  AND column_name = 'next_run_time'
            ) THEN
                ALTER TABLE bot_action RENAME COLUMN next_run_time TO next_review_time;
            END IF;
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'bot_action'
                  AND column_name = 'enter_trade'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'bot_action'
                  AND column_name = 'enter'
            ) THEN
                ALTER TABLE bot_action RENAME COLUMN enter_trade TO enter;
            END IF;
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'bot_action'
                  AND column_name = 'manage_trade'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'bot_action'
                  AND column_name = 'manage'
            ) THEN
                ALTER TABLE bot_action RENAME COLUMN manage_trade TO manage;
            END IF;
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'bot_action'
                  AND column_name = 'exit_trade'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'bot_action'
                  AND column_name = 'exit_action'
            ) THEN
                ALTER TABLE bot_action RENAME COLUMN exit_trade TO exit_action;
            ELSIF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'bot_action'
                  AND column_name = 'exit'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'bot_action'
                  AND column_name = 'exit_action'
            ) THEN
                ALTER TABLE bot_action RENAME COLUMN exit TO exit_action;
            END IF;
        END $$
    """)
    cursor.execute("""
        ALTER TABLE bot_action
            ADD COLUMN IF NOT EXISTS next_review_time VARCHAR(32),
            ADD COLUMN IF NOT EXISTS action_type VARCHAR(10),
            ADD COLUMN IF NOT EXISTS enter JSONB,
            ADD COLUMN IF NOT EXISTS manage JSONB,
            ADD COLUMN IF NOT EXISTS exit_action JSONB
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS current_positions (
            id            SERIAL PRIMARY KEY,
            symbol        VARCHAR(20)     NOT NULL,
            ticket        BIGINT          NOT NULL,
            asset         VARCHAR(20)     NOT NULL,
            direction     VARCHAR(10)     NOT NULL,
            entry_price   DECIMAL(18, 5)  NOT NULL,
            current_price DECIMAL(18, 5),
            stop_loss     DECIMAL(18, 5),
            take_profit   DECIMAL(18, 5),
            lot_size      DECIMAL(10, 2)  NOT NULL,
            entry_time    TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(symbol, ticket)
        )
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
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_account_snapshots_symbol_strategy_created ON account_snapshots(symbol, strategy_name, created_at DESC)")

    # Test / audit inputs — one append-only row per run per table (linked by run_id).
    _test_table_ddl = """
        CREATE TABLE IF NOT EXISTS {table} (
            id              SERIAL PRIMARY KEY,
            run_id          UUID         NOT NULL,
            magic_number    BIGINT       NOT NULL,
            run_type        VARCHAR(10)  NOT NULL,
            symbol          VARCHAR(20)  NOT NULL,
            strategy_name   VARCHAR(100) NOT NULL DEFAULT '',
            payload         JSONB,
            content         TEXT,
            created_at      TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
    """
    for table in (
        "test_macro",
        "test_ohlc",
        "test_chart",
        "test_sod",
        "test_intraday",
        "test_output",
    ):
        cursor.execute(_test_table_ddl.format(table=table))
        cursor.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_magic_created "
            f"ON {table}(magic_number, created_at DESC)"
        )
        cursor.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_run_id ON {table}(run_id)"
        )

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_analysis_notes_lookup "
        "ON analysis_notes(magic_number, symbol, strategy_name)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_bot_action_magic ON bot_action(magic_number)"
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
# One row per (magic_number, symbol, strategy_name).
# =============================================================================

def get_analysis_record(
    magic_number: int,
    symbol: str,
    strategy_name: str = '',
) -> Optional[Dict[str, Any]]:
    """Load sod_analysis and intraday_analysis for an EA instance."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT sod_analysis, intraday_analysis, created_at, updated_at
            FROM analysis_notes
            WHERE magic_number = %s AND symbol = %s AND strategy_name = %s
        """, (magic_number, symbol, strategy_name or ''))
        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if not row:
            return None

        return {
            "magic_number": magic_number,
            "symbol": symbol,
            "strategy_name": strategy_name or '',
            "sod_analysis": row[0],
            "intraday_analysis": row[1],
            "_db_created_at": row[2].isoformat() if row[2] else None,
            "_db_updated_at": row[3].isoformat() if row[3] else None,
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
            ON CONFLICT (magic_number, symbol, strategy_name) DO UPDATE SET
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
            ON CONFLICT (magic_number, symbol, strategy_name) DO UPDATE SET
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
            WHERE magic_number = %s AND symbol = %s AND strategy_name = %s
        """, (magic_number, symbol, strategy_name or ''))
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
# BOT ACTION
# One row per magic_number — scheduling and trade execution payloads for the EA.
# =============================================================================

def get_bot_action(magic_number: int) -> Optional[Dict[str, Any]]:
    """Load bot_action row for an EA magic number."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT magic_number, next_review_time, action_type, enter, manage, exit_action,
                   created_at, updated_at
            FROM bot_action
            WHERE magic_number = %s
        """, (magic_number,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if not row:
            return None

        return {
            "magic_number": int(row[0]),
            "next_review_time": row[1],
            "action_type": row[2],
            "enter": row[3],
            "manage": row[4],
            "exit": row[5],  # API key; DB column is exit_action
            "_db_created_at": row[6].isoformat() if row[6] else None,
            "_db_updated_at": row[7].isoformat() if row[7] else None,
        }

    except Exception as e:
        print(f"[db] get_bot_action error: {e}")
        return None


def save_bot_action(
    magic_number: int,
    next_review_time: Optional[str],
    action_type: Optional[str] = None,
    enter: Optional[Dict[str, Any]] = None,
    manage: Optional[Dict[str, Any]] = None,
    exit: Optional[Dict[str, Any]] = None,
) -> bool:
    """Upsert bot_action for an EA magic number (full replace each run)."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO bot_action
                (magic_number, next_review_time, action_type, enter, manage, exit_action, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (magic_number) DO UPDATE SET
                next_review_time = EXCLUDED.next_review_time,
                action_type      = EXCLUDED.action_type,
                enter            = EXCLUDED.enter,
                manage           = EXCLUDED.manage,
                exit_action      = EXCLUDED.exit_action,
                updated_at       = CURRENT_TIMESTAMP
        """, (
            magic_number,
            next_review_time,
            action_type,
            json.dumps(enter) if enter is not None else None,
            json.dumps(manage) if manage is not None else None,
            json.dumps(exit) if exit is not None else None,
        ))
        conn.commit()
        cursor.close()
        conn.close()
        return True

    except Exception as e:
        print(f"[db] save_bot_action error: {e}")
        return False


# =============================================================================
# TEST INPUTS (append-only audit log per AI run)
# =============================================================================

def _test_insert(
    cursor,
    table: str,
    run_id: str,
    magic_number: int,
    run_type: str,
    symbol: str,
    strategy_name: str,
    payload: Any = None,
    content: Optional[str] = None,
) -> None:
    payload_json = None
    if payload is not None:
        if isinstance(payload, (dict, list)):
            payload_json = json.dumps(payload)
        elif isinstance(payload, str):
            content = content or payload
        else:
            payload_json = json.dumps(payload)

    cursor.execute(
        f"""
        INSERT INTO {table}
            (run_id, magic_number, run_type, symbol, strategy_name, payload, content)
        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
        """,
        (
            run_id,
            magic_number,
            run_type,
            symbol,
            strategy_name or "",
            payload_json,
            content,
        ),
    )


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
    run_id: Optional[str] = None,
) -> Optional[str]:
    """
    Persist all AI inputs and the final flat output for a trading run (testing/audit).

    run_type: 'sod' | 'intraday'
    Returns run_id (UUID string) on success.
    """
    run_id = run_id or str(uuid.uuid4())
    run_type = (run_type or "").lower()
    if run_type not in ("sod", "intraday"):
        raise ValueError("run_type must be 'sod' or 'intraday'")

    chart_content = None
    chart_payload = None
    if isinstance(chart, str):
        chart_content = chart
    elif chart is not None:
        chart_payload = chart

    output_payload = {
        "flat": flat_output,
        "raw_gpt_response": raw_gpt_response,
    }

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        _test_insert(
            cursor, "test_macro", run_id, magic_number, run_type, symbol, strategy_name,
            payload=macro,
        )
        _test_insert(
            cursor, "test_ohlc", run_id, magic_number, run_type, symbol, strategy_name,
            payload=ohlc,
        )
        _test_insert(
            cursor, "test_chart", run_id, magic_number, run_type, symbol, strategy_name,
            payload=chart_payload,
            content=chart_content,
        )

        prompt_table = "test_sod" if run_type == "sod" else "test_intraday"
        _test_insert(
            cursor, prompt_table, run_id, magic_number, run_type, symbol, strategy_name,
            content=system_prompt,
        )

        _test_insert(
            cursor, "test_output", run_id, magic_number, run_type, symbol, strategy_name,
            payload=output_payload,
        )

        conn.commit()
        cursor.close()
        conn.close()
        print(f"[db] test_inputs saved run_id={run_id} ({run_type})")
        return run_id

    except Exception as e:
        print(f"[db] save_test_run error: {e}")
        return None


def clear_bot_action_trades(magic_number: int) -> bool:
    """Clear trade payloads after EA execution; keeps next_review_time."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE bot_action
            SET action_type = NULL, enter = NULL, manage = NULL, exit_action = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE magic_number = %s
        """, (magic_number,))
        conn.commit()
        cursor.close()
        conn.close()
        return True

    except Exception as e:
        print(f"[db] clear_bot_action_trades error: {e}")
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

def store_current_positions(symbol: str, positions: List[Dict[str, Any]]) -> bool:
    """Replace all positions for a symbol with the new list from the EA."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("DELETE FROM current_positions WHERE symbol = %s", (symbol,))

        for pos in positions:
            cursor.execute("""
                INSERT INTO current_positions
                (symbol, ticket, asset, direction, entry_price, current_price,
                 stop_loss, take_profit, lot_size, entry_time, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            """, (
                symbol,
                pos.get("ticket"),
                pos.get("asset"),
                pos.get("direction"),
                pos.get("entry_price"),
                pos.get("current_price"),
                pos.get("stop_loss"),
                pos.get("take_profit"),
                pos.get("lot_size"),
                pos.get("entry_time")
            ))

        conn.commit()
        cursor.close()
        conn.close()
        print(f"[db] Stored {len(positions)} position(s) for {symbol}")
        return True

    except Exception as e:
        print(f"[db] store_current_positions error: {e}")
        return False


def get_current_positions(symbol: str) -> List[Dict[str, Any]]:
    """Return all live positions for a symbol."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ticket, asset, direction, entry_price, current_price,
                   stop_loss, take_profit, lot_size, entry_time, updated_at
            FROM current_positions
            WHERE symbol = %s
            ORDER BY entry_time ASC
        """, (symbol,))
        results = cursor.fetchall()
        cursor.close()
        conn.close()

        return [{
            "ticket":        int(row[0]),
            "asset":         row[1],
            "direction":     row[2],
            "entry_price":   float(row[3]),
            "current_price": float(row[4]) if row[4] else None,
            "stop_loss":     float(row[5]) if row[5] else None,
            "take_profit":   float(row[6]) if row[6] else None,
            "lot_size":      float(row[7]),
            "entry_time":    row[8].isoformat() if row[8] else None,
            "_db_updated_at": row[9].isoformat() if row[9] else None
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
# One row per SOD/intraday run. Used to build account context for the AI
# (summary + first 10 and last 10 snapshots). All PnL values can be null.
# =============================================================================

def save_account_snapshot(
    symbol: str,
    strategy_name: str,
    account_size: Optional[float] = None,
    realised_pnl: Optional[float] = None,
    unrealised_pnl: Optional[float] = None,
    today_realised_pnl: Optional[float] = None,
    week_pnl: Optional[float] = None,
    month_pnl: Optional[float] = None,
) -> bool:
    """Append one account snapshot for this run (symbol + strategy)."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO account_snapshots
            (symbol, strategy_name, account_size, realised_pnl, unrealised_pnl,
             today_realised_pnl, week_pnl, month_pnl)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
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


def get_account_context_for_analysis(symbol: str, strategy_name: str) -> Dict[str, Any]:
    """
    Return account context for the AI: latest summary plus first 10 and last 10
    snapshots (by created_at). Used when building SOD/intraday context.
    """
    result = {
        "account_size": None,
        "realised_pnl": None,
        "unrealised_pnl": None,
        "today_realised_pnl": None,
        "week_pnl": None,
        "month_pnl": None,
        "first_10": [],
        "last_10": [],
    }
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        scoped_strategy = strategy_name or ''

        # Latest snapshot for summary
        cursor.execute("""
            SELECT account_size, realised_pnl, unrealised_pnl,
                   today_realised_pnl, week_pnl, month_pnl, created_at
            FROM account_snapshots
            WHERE symbol = %s AND strategy_name = %s
            ORDER BY created_at DESC
            LIMIT 1
        """, (symbol, scoped_strategy))
        row = cursor.fetchone()
        if row:
            result["account_size"] = float(row[0]) if row[0] is not None else None
            result["realised_pnl"] = float(row[1]) if row[1] is not None else None
            result["unrealised_pnl"] = float(row[2]) if row[2] is not None else None
            result["today_realised_pnl"] = float(row[3]) if row[3] is not None else None
            result["week_pnl"] = float(row[4]) if row[4] is not None else None
            result["month_pnl"] = float(row[5]) if row[5] is not None else None

        # First 10 (oldest) and last 10 (newest) snapshots — two separate queries to avoid duplicates
        def row_to_dict(r):
            return {
                "account_size": float(r[0]) if r[0] is not None else None,
                "realised_pnl": float(r[1]) if r[1] is not None else None,
                "unrealised_pnl": float(r[2]) if r[2] is not None else None,
                "today_realised_pnl": float(r[3]) if r[3] is not None else None,
                "week_pnl": float(r[4]) if r[4] is not None else None,
                "month_pnl": float(r[5]) if r[5] is not None else None,
                "created_at": r[6].isoformat() if r[6] else None,
            }
        cursor.execute("""
            SELECT account_size, realised_pnl, unrealised_pnl,
                   today_realised_pnl, week_pnl, month_pnl, created_at
            FROM account_snapshots
            WHERE symbol = %s AND strategy_name = %s
            ORDER BY created_at ASC
            LIMIT 10
        """, (symbol, scoped_strategy))
        result["first_10"] = [row_to_dict(r) for r in cursor.fetchall()]
        cursor.execute("""
            SELECT account_size, realised_pnl, unrealised_pnl,
                   today_realised_pnl, week_pnl, month_pnl, created_at
            FROM account_snapshots
            WHERE symbol = %s AND strategy_name = %s
            ORDER BY created_at DESC
            LIMIT 10
        """, (symbol, scoped_strategy))
        result["last_10"] = [row_to_dict(r) for r in cursor.fetchall()]

        cursor.close()
        conn.close()
    except Exception as e:
        print(f"[db] get_account_context_for_analysis error: {e}")
    return result
