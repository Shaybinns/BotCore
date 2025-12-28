# Memory package for InvestCore AI
# This file makes the memory directory a Python package

from .short_term_cache import (
    get_recent_conversation,
    add_to_recent_conversation,
    update_current_cache,
    get_current_cache,
    update_market_data,
    get_current_market_data,
    get_comprehensive_cache,
    get_user_data_summary
)

from .command_stack import (
    peek_stack,
    has_pending_steps,
    get_current_goal,
    build_command_stack_with_dependencies,
    execute_complete_stack,
    resume_stack_execution,
    get_required_commands,
    check_required_fields
)

from .running_state import (
    set_running,
    is_running,
    get_running_context,
    update_running_context,
    pause_execution,
    resume_execution,
    get_execution_summary,
    log_session_event
)

__all__ = [
    # Short term cache functions
    'get_recent_conversation',
    'add_to_recent_conversation', 
    'update_current_cache',
    'get_current_cache',
    'update_market_data',
    'get_current_market_data',
    'get_comprehensive_cache',
    'get_user_data_summary',
    
    # Command stack functions
    'peek_stack',
    'has_pending_steps',
    'get_current_goal',
    'build_command_stack_with_dependencies',
    'execute_complete_stack',
    'resume_stack_execution',
    'get_required_commands',
    'check_required_fields',
    
    # Running state functions
    'set_running',
    'is_running',
    'get_running_context',
    'update_running_context',
    'pause_execution',
    'resume_execution',
    'get_execution_summary',
    'log_session_event'
]
