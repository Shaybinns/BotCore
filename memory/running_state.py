from memory.short_term_cache import update_current_cache, get_current_cache
from datetime import datetime
import json

def set_running(user_id, status: bool, context: str = None, command: str = None):
    """Set running state with comprehensive context and store in database"""
    current_cache = get_current_cache(user_id)
    
    running_state = {
        "is_running": status,
        "status": "running" if status else "stopped",
        "timestamp": datetime.now().isoformat(),
        "context": context,
        "command": command,
        "session_start": current_cache.get("running_state", {}).get("session_start") if status else None,
        "session_end": None if status else datetime.now().isoformat()
    }
    
    if status and not current_cache.get("running_state", {}).get("session_start"):
        running_state["session_start"] = datetime.now().isoformat()
    
    # Update database with running state
    update_current_cache(user_id, {
        "running_state": running_state,
        "last_state_update": datetime.now().isoformat(),
        "active_sessions": 1 if status else 0
    })
    
    return running_state

def is_running(user_id):
    """Check if user has active running state from database"""
    current_cache = get_current_cache(user_id)
    running_state = current_cache.get("running_state", {})
    return running_state.get("is_running", False)

def get_running_context(user_id):
    """Get current running context from database"""
    current_cache = get_current_cache(user_id)
    running_state = current_cache.get("running_state", {})
    
    if running_state.get("is_running"):
        return {
            "context": running_state.get("context"),
            "command": running_state.get("command"),
            "session_start": running_state.get("session_start"),
            "duration": _calculate_duration(running_state.get("session_start"))
        }
    return None

def update_running_context(user_id, context: str = None, command: str = None, progress: str = None):
    """Update running context with new information"""
    current_cache = get_current_cache(user_id)
    running_state = current_cache.get("running_state", {})
    
    if running_state.get("is_running"):
        running_state.update({
            "context": context or running_state.get("context"),
            "command": command or running_state.get("command"),
            "last_update": datetime.now().isoformat(),
            "progress": progress
        })
        
        # Update database
        update_current_cache(user_id, {
            "running_state": running_state,
            "last_state_update": datetime.now().isoformat()
        })
        
        return running_state
    return None

def pause_execution(user_id, reason: str = None):
    """Pause execution while maintaining state"""
    current_cache = get_current_cache(user_id)
    running_state = current_cache.get("running_state", {})
    
    if running_state.get("is_running"):
        running_state.update({
            "is_running": False,
            "paused_at": datetime.now().isoformat(),
            "pause_reason": reason,
            "status": "paused"
        })
        
        # Update database
        update_current_cache(user_id, {
            "running_state": running_state,
            "last_state_update": datetime.now().isoformat(),
            "execution_status": "paused"
        })
        
        return running_state
    return None

def resume_execution(user_id):
    """Resume paused execution"""
    current_cache = get_current_cache(user_id)
    running_state = current_cache.get("running_state", {})
    
    if running_state.get("status") == "paused":
        running_state.update({
            "is_running": True,
            "resumed_at": datetime.now().isoformat(),
            "status": "running"
        })
        
        # Update database
        update_current_cache(user_id, {
            "running_state": running_state,
            "last_state_update": datetime.now().isoformat(),
            "execution_status": "running"
        })
        
        return running_state
    return None

def get_execution_summary(user_id):
    """Get comprehensive execution summary from database"""
    current_cache = get_current_cache(user_id)
    running_state = current_cache.get("running_state", {})
    
    if not running_state:
        return {
            "status": "no_execution",
            "total_sessions": 0,
            "current_session": None
        }
    
    return {
        "status": running_state.get("status", "unknown"),
        "is_running": running_state.get("is_running", False),
        "current_session": {
            "start": running_state.get("session_start"),
            "duration": _calculate_duration(running_state.get("session_start")),
            "context": running_state.get("context"),
            "command": running_state.get("command"),
            "progress": running_state.get("progress")
        },
        "last_update": running_state.get("last_update"),
        "session_history": current_cache.get("session_history", [])
    }

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

def log_session_event(user_id, event_type: str, details: str = None):
    """Log session events for tracking"""
    current_cache = get_current_cache(user_id)
    session_history = current_cache.get("session_history", [])
    
    event = {
        "timestamp": datetime.now().isoformat(),
        "event_type": event_type,
        "details": details
    }
    
    session_history.append(event)
    
    # Keep only last 50 events
    if len(session_history) > 50:
        session_history = session_history[-50:]
    
    # Update database
    update_current_cache(user_id, {
        "session_history": session_history,
        "last_event_log": datetime.now().isoformat()
    })
    
    return event
