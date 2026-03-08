"""
Database Layer - PostgreSQL Integration

5 tables:
  analysis_notes    — AI analysis outputs (SOD, intraday, market data cache)
  current_positions — live MT5 positions, fully replaced on each EA update
  trade_events      — append-only audit log of EA execution confirmations
  users             — chat interface user accounts and message history
  strategies        — named strategy prompts, selectable per EA instance
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

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS analysis_notes (
            id          SERIAL PRIMARY KEY,
            symbol      VARCHAR(20)  NOT NULL,
            note_type   VARCHAR(50)  NOT NULL,
            summary     TEXT,
            key_points  JSONB,
            full_response JSONB,
            created_at  TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(symbol, note_type)
        )
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

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_analysis_notes_symbol ON analysis_notes(symbol, note_type)")
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
# One row per (symbol, note_type). Always upserted — never accumulates.
# note_type values in use:
#   sod_note         — full SOD analysis result
#   last_run_note    — most recent intraday result (cleared each SOD)
#   market_data_note — market data cache (refreshed every ~4h by intraday)
# =============================================================================

def get_analysis_note(symbol: str, note_type: str) -> Optional[Dict[str, Any]]:
    """Load a note. Returns the full_response dict with _db_created_at injected, or None."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT full_response, created_at
            FROM analysis_notes
            WHERE symbol = %s AND note_type = %s
        """, (symbol, note_type))
        result = cursor.fetchone()
        cursor.close()
        conn.close()

        if not result:
            return None

        full_response = result[0] or {}
        full_response["_db_created_at"] = result[1].isoformat() if result[1] else None
        return full_response

    except Exception as e:
        print(f"[db] get_analysis_note error: {e}")
        return None


def save_analysis_note(symbol: str, note_type: str, full_response: Dict[str, Any]) -> bool:
    """Upsert a note. Overwrites any existing row for this (symbol, note_type)."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        summary    = full_response.get("decision", {}).get("summary", "")
        key_points = full_response.get("decision", {}).get("key_points", [])

        cursor.execute("""
            INSERT INTO analysis_notes (symbol, note_type, summary, key_points, full_response, created_at)
            VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (symbol, note_type) DO UPDATE SET
                summary       = EXCLUDED.summary,
                key_points    = EXCLUDED.key_points,
                full_response = EXCLUDED.full_response,
                created_at    = CURRENT_TIMESTAMP
        """, (symbol, note_type, summary, json.dumps(key_points), json.dumps(full_response)))

        conn.commit()
        cursor.close()
        conn.close()
        return True

    except Exception as e:
        print(f"[db] save_analysis_note error: {e}")
        return False


def clear_analysis_notes(symbol: str, note_types: List[str]) -> bool:
    """Delete specific note types for a symbol (e.g. clear last_run_note at SOD)."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        placeholders = ','.join(['%s'] * len(note_types))
        cursor.execute(
            f"DELETE FROM analysis_notes WHERE symbol = %s AND note_type IN ({placeholders})",
            [symbol] + note_types
        )
        conn.commit()
        cursor.close()
        conn.close()
        return True

    except Exception as e:
        print(f"[db] clear_analysis_notes error: {e}")
        return False


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
