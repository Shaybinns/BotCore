import psycopg2
import os
import json
from datetime import datetime, timedelta, date
from typing import Dict, List, Any
from dotenv import load_dotenv

load_dotenv()

# Database table name constant
SHORT_TERM_DB = "short_term_memory"

def get_db_connection():
    """Get connection to database using Railway's injected DATABASE_URL"""
    return psycopg2.connect(os.getenv('DATABASE_URL'))

def add_to_recent_conversation(user_id: str, message: str):
    """
    Add message to recent conversation
    Keeps only the last 20 messages total
    Sets expires_at to 24 hours from creation
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get existing messages
        cursor.execute(f"""
            SELECT recent_messages FROM {SHORT_TERM_DB}
            WHERE user_id = %s
        """, (user_id,))
        
        result = cursor.fetchone()
        if result and result[0]:
            messages = result[0]
            # Use existing created_at, update expires_at to 24 hours from now
            expires_at = (datetime.now() + timedelta(hours=24)).date()
        else:
            messages = []
            # New user, set both created_at and expires_at
            created_at = datetime.now().date()
            expires_at = (datetime.now() + timedelta(hours=24)).date()
        
        # Add new message (just the text, no role categorization)
        messages.append(message)
        
        # Keep only last 20 messages
        if len(messages) > 20:
            messages = messages[-20:]
        
        # Update database with proper timestamps
        if result:
            # Existing user, update messages and expires_at
            cursor.execute(f"""
                UPDATE {SHORT_TERM_DB}
                SET recent_messages = %s, expires_at = %s
                WHERE user_id = %s
            """, (json.dumps(messages), expires_at, user_id))
        else:
            # New user, insert with created_at and expires_at
            cursor.execute(f"""
                INSERT INTO {SHORT_TERM_DB}
                (user_id, recent_messages, created_at, expires_at)
                VALUES (%s, %s, %s, %s)
            """, (user_id, json.dumps(messages), created_at, expires_at))
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
        
    except Exception as e:
        print(f"Error adding to conversation: {e}")
        return False

def get_recent_conversation(user_id: str) -> str:
    """Get recent conversation as simple text"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(f"""
            SELECT recent_messages FROM {SHORT_TERM_DB}
            WHERE user_id = %s AND expires_at > CURRENT_DATE
        """, (user_id,))
        
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if not result or not result[0]:
            return ""
        
        # Return messages as simple text, one per line
        messages = result[0]
        return "\n".join(messages)
        
    except Exception as e:
        print(f"Error retrieving conversation: {e}")
        return ""

def update_current_cache(user_id: str, cache_data: Dict[str, Any]):
    """Update current cache with new data"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get existing cache
        cursor.execute(f"""
            SELECT current_cache FROM {SHORT_TERM_DB}
            WHERE user_id = %s AND expires_at > CURRENT_DATE
        """, (user_id,))
        
        result = cursor.fetchone()
        existing_cache = result[0] if result and result[0] else {}
        
        # Merge new cache data
        updated_cache = {**existing_cache, **cache_data}
        
        # Update database
        cursor.execute(f"""
            INSERT INTO {SHORT_TERM_DB} (user_id, current_cache, created_at, expires_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) 
            DO UPDATE SET 
                current_cache = %s,
                expires_at = %s
        """, (user_id, json.dumps(updated_cache), datetime.now().date(), 
               (datetime.now() + timedelta(hours=24)).date(),
               json.dumps(updated_cache), 
               (datetime.now() + timedelta(hours=24)).date()))
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
        
    except Exception as e:
        print(f"Error updating cache: {e}")
        return False

def get_current_cache(user_id: str) -> Dict[str, Any]:
    """Get current cache data for user"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(f"""
            SELECT current_cache FROM {SHORT_TERM_DB}
            WHERE user_id = %s AND expires_at > CURRENT_DATE
        """, (user_id,))
        
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        return result[0] if result and result[0] else {}
        
    except Exception as e:
        print(f"Error retrieving cache: {e}")
        return {}

def update_market_data(user_id: str, market_data: Dict[str, Any]):
    """Update current market data context"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Add timestamp
        market_data["last_updated"] = datetime.now().isoformat()
        
        # Update database
        cursor.execute(f"""
            INSERT INTO {SHORT_TERM_DB} (user_id, current_market_data, created_at, expires_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) 
            DO UPDATE SET 
                current_market_data = %s,
                expires_at = %s
        """, (user_id, json.dumps(market_data), datetime.now().date(), 
               (datetime.now() + timedelta(hours=24)).date(),
               json.dumps(market_data), 
               (datetime.now() + timedelta(hours=24)).date()))
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
        
    except Exception as e:
        print(f"Error updating market data: {e}")
        return False

def get_current_market_data(user_id: str) -> Dict[str, Any]:
    """Get current market data for user"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(f"""
            SELECT current_market_data FROM {SHORT_TERM_DB}
            WHERE user_id = %s AND expires_at > CURRENT_DATE
        """, (user_id,))
        
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        return result[0] if result and result[0] else {}
        
    except Exception as e:
        print(f"Error retrieving market data: {e}")
        return {}

def cleanup_expired_entries():
    """Remove all expired entries (older than 24 hours)"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(f"""
            DELETE FROM {SHORT_TERM_DB}
            WHERE expires_at < CURRENT_DATE
        """)
        
        deleted_count = cursor.rowcount
        conn.commit()
        cursor.close()
        conn.close()
        
        print(f"Cleaned up {deleted_count} expired entries")
        return deleted_count
        
    except Exception as e:
        print(f"Error cleaning up expired entries: {e}")
        return 0

# Convenience functions
def update_command_stack(user_id: str, command_stack: List[Dict]):
    """Update command stack in cache"""
    return update_current_cache(user_id, {"command_stack": command_stack})

def get_command_stack(user_id: str) -> List[Dict]:
    """Get current command stack from cache"""
    cache = get_current_cache(user_id)
    return cache.get("command_stack", [])

def update_user_preferences(user_id: str, preferences: Dict[str, Any]):
    """Update user preferences in cache"""
    return update_current_cache(user_id, {"user_preferences": preferences})

def get_user_preferences(user_id: str) -> Dict[str, Any]:
    """Get user preferences from cache"""
    cache = get_current_cache(user_id)
    return cache.get("user_preferences", {})

def clear_user_data(user_id: str):
    """Clear all data for a user"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(f"""
            DELETE FROM {SHORT_TERM_DB}
            WHERE user_id = %s
        """, (user_id,))
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
        
    except Exception as e:
        print(f"Error clearing user data: {e}")
        return False

def get_user_data_summary(user_id: str) -> Dict[str, Any]:
    """Get comprehensive summary of user's data including expiry info and cache details"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(f"""
            SELECT recent_messages, current_cache, current_market_data, 
                   created_at, expires_at
            FROM {SHORT_TERM_DB}
            WHERE user_id = %s AND expires_at > CURRENT_DATE
        """, (user_id,))
        
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if not result:
            return {
                "user_id": user_id,
                "message_count": 0,
                "cache_keys": [],
                "market_data_keys": [],
                "created_at": None,
                "expires_at": None,
                "time_until_expiry": "No data",
                "cache_summary": {},
                "execution_status": "no_data"
            }
        
        messages, cache, market_data, created_at, expires_at = result
        
        # Calculate time until expiry
        time_until_expiry = expires_at - datetime.now().date()
        hours_remaining = time_until_expiry.days * 24
        
        # Enhanced cache summary
        cache_summary = {}
        if cache:
            # Command stack summary
            if "command_stack" in cache:
                command_stack = cache["command_stack"]
                cache_summary["command_stack"] = {
                    "total_commands": len(command_stack),
                    "pending": len([cmd for cmd in command_stack if cmd.get("status") == "pending"]),
                    "executing": len([cmd for cmd in command_stack if cmd.get("status") == "executing"]),
                    "completed": len([cmd for cmd in command_stack if cmd.get("status") == "done"]),
                    "current_goal": command_stack[0].get("goal") if command_stack else None
                }
            
            # Running state summary
            if "running_state" in cache:
                running_state = cache["running_state"]
                cache_summary["running_state"] = {
                    "is_running": running_state.get("is_running", False),
                    "status": running_state.get("status", "unknown"),
                    "context": running_state.get("context"),
                    "command": running_state.get("command"),
                    "session_duration": _calculate_duration(running_state.get("session_start"))
                }
            
            # General cache info
            cache_summary["last_update"] = cache.get("last_stack_update") or cache.get("last_state_update")
            cache_summary["active_sessions"] = cache.get("active_sessions", 0)
            cache_summary["pending_commands"] = cache.get("pending_commands", 0)
        
        return {
            "user_id": user_id,
            "message_count": len(messages) if messages else 0,
            "cache_keys": list(cache.keys()) if cache else [],
            "market_data_keys": list(market_data.keys()) if market_data else [],
            "created_at": created_at,
            "expires_at": expires_at,
            "time_until_expiry": f"{hours_remaining:.1f} hours remaining",
            "cache_summary": cache_summary,
            "execution_status": cache_summary.get("running_state", {}).get("status", "idle")
        }
        
    except Exception as e:
        print(f"Error getting user data summary: {e}")
        return {}

def _calculate_duration(start_time_str):
    """Calculate duration from start time string"""
    if not start_time_str:
        return None
    
    try:
        start_time = datetime.fromisoformat(start_time_str)
        duration = datetime.now() - start_time
        return str(duration).split('.')[0]  # Remove microseconds
    except:
        return None

def get_comprehensive_cache(user_id: str) -> Dict[str, Any]:
    """Get comprehensive cache data with detailed breakdowns"""
    try:
        cache = get_current_cache(user_id)
        
        if not cache:
            return {
                "user_id": user_id,
                "status": "no_cache",
                "timestamp": datetime.now().isoformat()
            }
        
        # Enhanced breakdown
        breakdown = {
            "user_id": user_id,
            "timestamp": datetime.now().isoformat(),
            "cache_size": len(cache),
            "last_update": cache.get("last_stack_update") or cache.get("last_state_update"),
            
            # Command stack details
            "command_stack": {
                "overview": cache.get("command_stack", []),
                "summary": cache.get("command_stack_summary", {}),
                "active_goals": cache.get("active_goals", []),
                "pending_count": cache.get("pending_commands", 0),
                "completed_count": cache.get("completed_commands", 0)
            },
            
            # Running state details
            "running_state": cache.get("running_state", {}),
            
            # Market data
            "market_data": cache.get("current_market_data", {}),
            
            # Session tracking
            "session_history": cache.get("session_history", []),
            "active_sessions": cache.get("active_sessions", 0),
            
            # Performance metrics
            "performance": {
                "last_completion": cache.get("last_completion"),
                "stack_cleaned": cache.get("stack_cleaned"),
                "execution_status": cache.get("execution_status")
            }
        }
        
        return breakdown
        
    except Exception as e:
        print(f"Error getting comprehensive cache: {e}")
        return {"error": str(e)}
