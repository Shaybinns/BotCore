"""
Database Layer - PostgreSQL Integration

Manages:
- Locked levels/zones
- Active setup states
- Trade events/history
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
    """Initialize database tables."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Locked levels table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS locked_levels (
            id SERIAL PRIMARY KEY,
            symbol VARCHAR(20) NOT NULL,
            session VARCHAR(50),
            level_type VARCHAR(20) NOT NULL,
            price DECIMAL(18, 5) NOT NULL,
            zone_top DECIMAL(18, 5),
            zone_bottom DECIMAL(18, 5),
            timeframe VARCHAR(10),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            invalidated_at TIMESTAMP WITH TIME ZONE,
            metadata JSONB,
            UNIQUE(symbol, session, price, level_type)
        )
    """)
    
    # Active setups table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS active_setups (
            setup_id VARCHAR(100) PRIMARY KEY,
            symbol VARCHAR(20) NOT NULL,
            session VARCHAR(50),
            phase VARCHAR(50) NOT NULL,
            state_data JSONB,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP WITH TIME ZONE
        )
    """)
    
    # Trade events table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trade_events (
            id SERIAL PRIMARY KEY,
            setup_id VARCHAR(100),
            symbol VARCHAR(20) NOT NULL,
            event_type VARCHAR(50) NOT NULL,
            event_data JSONB,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Create indexes
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_locked_levels_symbol_session 
        ON locked_levels(symbol, session)
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_active_setups_symbol 
        ON active_setups(symbol)
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_trade_events_setup_id 
        ON trade_events(setup_id)
    """)
    
    conn.commit()
    cursor.close()
    conn.close()


def get_locked_levels(
    symbol: str,
    session: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Get locked levels for a symbol and session."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if session:
            cursor.execute("""
                SELECT id, level_type, price, zone_top, zone_bottom, 
                       timeframe, metadata
                FROM locked_levels
                WHERE symbol = %s AND session = %s AND invalidated_at IS NULL
                ORDER BY created_at DESC
            """, (symbol, session))
        else:
            cursor.execute("""
                SELECT id, level_type, price, zone_top, zone_bottom, 
                       timeframe, metadata
                FROM locked_levels
                WHERE symbol = %s AND invalidated_at IS NULL
                ORDER BY created_at DESC
            """, (symbol,))
        
        results = cursor.fetchall()
        cursor.close()
        conn.close()
        
        levels = []
        for row in results:
            levels.append({
                "id": row[0],
                "type": row[1],
                "price": float(row[2]),
                "zone_top": float(row[3]) if row[3] else None,
                "zone_bottom": float(row[4]) if row[4] else None,
                "timeframe": row[5],
                "metadata": row[6] or {}
            })
        
        return levels
        
    except Exception as e:
        print(f"Error getting locked levels: {e}")
        return []


def save_locked_levels(
    symbol: str,
    levels: List[Dict[str, Any]],
    session: Optional[str] = None
) -> bool:
    """Save locked levels to database."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        for level in levels:
            cursor.execute("""
                INSERT INTO locked_levels 
                (symbol, session, level_type, price, zone_top, zone_bottom, timeframe, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (symbol, session, price, level_type)
                DO UPDATE SET
                    zone_top = EXCLUDED.zone_top,
                    zone_bottom = EXCLUDED.zone_bottom,
                    timeframe = EXCLUDED.timeframe,
                    metadata = EXCLUDED.metadata,
                    invalidated_at = NULL
            """, (
                symbol,
                session,
                level.get("type", "level"),
                level.get("price"),
                level.get("zone_top"),
                level.get("zone_bottom"),
                level.get("timeframe"),
                json.dumps(level.get("metadata", {}))
            ))
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
        
    except Exception as e:
        print(f"Error saving locked levels: {e}")
        return False


def get_active_setup(
    symbol: str,
    setup_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Get active setup state."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if setup_id:
            cursor.execute("""
                SELECT setup_id, symbol, session, phase, state_data, 
                       created_at, updated_at
                FROM active_setups
                WHERE setup_id = %s AND completed_at IS NULL
            """, (setup_id,))
        else:
            cursor.execute("""
                SELECT setup_id, symbol, session, phase, state_data, 
                       created_at, updated_at
                FROM active_setups
                WHERE symbol = %s AND completed_at IS NULL
                ORDER BY created_at DESC
                LIMIT 1
            """, (symbol,))
        
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if not result:
            return None
        
        return {
            "setup_id": result[0],
            "symbol": result[1],
            "session": result[2],
            "phase": result[3],
            "state_data": result[4] or {},
            "created_at": result[5].isoformat() if result[5] else None,
            "updated_at": result[6].isoformat() if result[6] else None
        }
        
    except Exception as e:
        print(f"Error getting active setup: {e}")
        return None


def save_setup_state(
    symbol: str,
    setup_id: str,
    state: Dict[str, Any],
    session: Optional[str] = None
) -> bool:
    """Save or update setup state."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        phase = state.get("phase", "WATCHING")
        
        cursor.execute("""
            INSERT INTO active_setups 
            (setup_id, symbol, session, phase, state_data, updated_at)
            VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (setup_id)
            DO UPDATE SET
                phase = EXCLUDED.phase,
                state_data = EXCLUDED.state_data,
                updated_at = CURRENT_TIMESTAMP
        """, (
            setup_id,
            symbol,
            session,
            phase,
            json.dumps(state)
        ))
        
        # Mark as completed if phase is STAND_DOWN
        if phase == "STAND_DOWN":
            cursor.execute("""
                UPDATE active_setups
                SET completed_at = CURRENT_TIMESTAMP
                WHERE setup_id = %s
            """, (setup_id,))
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
        
    except Exception as e:
        print(f"Error saving setup state: {e}")
        return False


def save_trade_event(
    setup_id: Optional[str],
    symbol: str,
    event_type: str,
    event_data: Dict[str, Any]
) -> bool:
    """Save trade event to history."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO trade_events 
            (setup_id, symbol, event_type, event_data)
            VALUES (%s, %s, %s, %s)
        """, (
            setup_id,
            symbol,
            event_type,
            json.dumps(event_data)
        ))
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
        
    except Exception as e:
        print(f"Error saving trade event: {e}")
        return False

