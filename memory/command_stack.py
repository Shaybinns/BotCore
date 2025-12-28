from memory.short_term_cache import update_current_cache, get_current_cache
from datetime import datetime
import json


def peek_stack(user_id):
    """Get the current pending command from database"""
    current_cache = get_current_cache(user_id)
    command_stack = current_cache.get("command_stack", [])
    
    if command_stack:
        return command_stack[0]
    return None




def has_pending_steps(user_id):
    """Check if there are pending steps in database"""
    current_cache = get_current_cache(user_id)
    command_stack = current_cache.get("command_stack", [])
    
    return any(step["status"] in ["pending", "executing"] for step in command_stack)


def get_current_goal(user_id):
    """Get current goal from database"""
    current_cache = get_current_cache(user_id)
    command_stack = current_cache.get("command_stack", [])
    
    if command_stack:
        return command_stack[0].get("goal")
    return None



def get_required_commands(command_name):
    """Get required commands for a given command"""
    # Define command dependencies
    command_dependencies = {
        "asset_assess": ["get_asset_info", "market_assess"],
        "market_rec": ["market_assess"],
        "portfolio_screener": ["get_investment_criteria", "market_assess"],
        "portfolio_construction": ["get_investment_criteria", "portfolio_screener", "market_assess"],
        "portfolio_calculation": ["portfolio_construction"],
        "holdings_analysis": ["get_investment_criteria", "get_user_portfolio", "market_assess", "portfolio_calculation"],
        "simulate_portfolio": ["get_investment_criteria", "market_assess"],
        "create_portfolio": ["get_investment_criteria", "portfolio_screener", "portfolio_construction", "portfolio_calculation", "simulate_portfolio", "build_pie"],
        "optimise_portfolio": ["get_investment_criteria", "portfolio_calculation", "simulate_portfolio", "rebalance_pie"],
        "analyse_portfolio": ["get_investment_criteria", "holdings_analysis", "portfolio_screener", "portfolio_calculation", "simulate_portfolio", "rebalance_pie"]
    }
    
    return command_dependencies.get(command_name, [])

def build_command_stack_with_dependencies(user_id, main_command, args, goal=None):
    """Build a complete command stack with all required dependencies"""
    # Get required commands for the main command
    required_commands = get_required_commands(main_command)
    
    # Clear any existing stack
    current_cache = get_current_cache(user_id)
    command_stack = []
    
    # Add required commands first (in order)
    for req_command in required_commands:
        # Check if we already have recent results for this command
        # For now, we'll add all required commands - optimization can come later
        
        # Pass relevant args to required commands based on their requirements
        req_args = {}
        
        # Define what args each command typically needs
        command_arg_mapping = {
            "get_asset_info": ["symbol"],
            "get_financials": ["symbol"], 
            "get_earnings": ["symbol"],
            "sector_assess": ["sector"],
            "get_investment_criteria": ["user_id"],
            "portfolio_screener": ["filters"],
            "screen_assets": ["filters"],
            "search_web": ["query"]
        }
        
        # Get the required args for this command
        needed_args = command_arg_mapping.get(req_command, [])
        
        # Pass any available args that this command needs
        for arg_name in needed_args:
            if arg_name in args:
                req_args[arg_name] = args[arg_name]
        
        # Special case: if the main command has a symbol, pass it to asset-related commands
        if "symbol" in args and req_command in ["get_asset_info", "get_financials", "get_earnings"]:
            req_args["symbol"] = args["symbol"]
        
        # Special case: if the main command has a sector, pass it to sector commands
        if "sector" in args and req_command == "sector_assess":
            req_args["sector"] = args["sector"]
        
        # Always pass user_id to commands that need it
        if "user_id" in args and req_command in ["asset_assess", "market_rec"]:
            req_args["user_id"] = args["user_id"]
        
        new_command = {
            "command": req_command,
            "args": req_args,  # Pass relevant args to required commands
            "goal": goal,
            "status": "pending",
            "timestamp": datetime.now().isoformat(),
            "created_at": datetime.now().isoformat(),
            "execution_start": None,
            "execution_end": None,
            "result": None,
            "error": None,
            "completion_notes": None,
            "is_required": True  # Mark as required command
        }
        command_stack.append(new_command)
    
    # Add the main command last
    main_command_obj = {
        "command": main_command,
        "args": args,
        "goal": goal,
        "status": "pending",
        "timestamp": datetime.now().isoformat(),
        "created_at": datetime.now().isoformat(),
        "execution_start": None,
        "execution_end": None,
        "result": None,
        "error": None,
        "completion_notes": None,
        "is_required": False  # Main command
    }
    command_stack.append(main_command_obj)
    
    # Update database with new stack
    update_current_cache(user_id, {
        "command_stack": command_stack,
        "last_stack_update": datetime.now().isoformat(),
        "active_goals": [cmd.get("goal") for cmd in command_stack if cmd.get("goal")],
        "pending_commands": len([cmd for cmd in command_stack if cmd["status"] == "pending"]),
        "completed_commands": 0,
        "stack_type": "dependency_chain"
    })
    
    return command_stack

def check_required_fields(command_name, args):
    """Check if a command has all its required fields"""
    try:
        # Try to import the command module and get required fields
        module = __import__(f"commands.{command_name}", fromlist=["get_required_fields"])
        required_fields = module.get_required_fields()
        
        if isinstance(required_fields, dict):
            # Dictionary format: {"field": {"prompt": "..."}}
            missing_fields = [field for field in required_fields if field not in args]
        elif isinstance(required_fields, list):
            # List format: ["field1", "field2"]
            missing_fields = [field for field in required_fields if field not in args]
        else:
            # No required fields defined
            missing_fields = []
        
        return missing_fields
        
    except (ImportError, AttributeError):
        # Command doesn't have required fields defined
        return []

def execute_complete_stack(user_id, command_engine):
    """Execute the complete command stack continuously until empty"""
    from memory.long_term_db import save_result
    from utils.storage_summariser import summarise_result
    from utils.output_summariser import summarise_output
    from memory.data_collector import needs_more_input, start_data_collection
    
    current_cache = get_current_cache(user_id)
    command_stack = current_cache.get("command_stack", [])
    
    results = []
    errors = []
    
    # Execute all commands in the stack
    for i, command in enumerate(command_stack):
        if command["status"] == "pending":
            try:
                # Check for required fields before executing
                missing_fields = check_required_fields(command["command"], command["args"])
                
                if missing_fields:
                    # Missing fields - trigger data collection
                    command["status"] = "waiting_for_input"
                    command["missing_fields"] = missing_fields
                    
                    # Start data collection for this command
                    start_data_collection(user_id, command["command"], command["args"], missing_fields, {})
                    
                    # Return early to let data collector handle the input
                    return {
                        "results": results,
                        "errors": errors,
                        "main_command_result": None,
                        "needs_input": True,
                        "missing_fields": missing_fields,
                        "current_command": command["command"]
                    }
                
                # Mark as executing
                command["status"] = "executing"
                command["execution_start"] = datetime.now().isoformat()
                
                # Execute the command
                result = command_engine(command["command"], command["args"])
                
                # Mark as complete
                command["status"] = "done"
                command["execution_end"] = datetime.now().isoformat()
                command["result"] = result
                
                # Save result to long-term memory
                summary = summarise_result(command["command"], result)
                save_result(user_id, summary)
                
                # Store result for later use
                results.append({
                    "command": command["command"],
                    "result": result,
                    "is_required": command.get("is_required", False)
                })
                
                # Update cache immediately so next command can access this data
                # Get existing execution_results and append new result
                current_cache = get_current_cache(user_id)
                existing_results = current_cache.get("execution_results", [])
                existing_results.append({
                    "command": command["command"],
                    "result": result,
                    "is_required": command.get("is_required", False)
                })
                
                update_current_cache(user_id, {
                    "execution_results": existing_results,
                    "last_stack_update": datetime.now().isoformat()
                })
                
            except Exception as e:
                # Mark as error
                command["status"] = "error"
                command["execution_end"] = datetime.now().isoformat()
                command["error"] = str(e)
                
                errors.append({
                    "command": command["command"],
                    "error": str(e),
                    "is_required": command.get("is_required", False)
                })
    
    # Update the database with final stack state
    update_current_cache(user_id, {
        "command_stack": command_stack,
        "last_stack_update": datetime.now().isoformat(),
        "stack_execution_complete": datetime.now().isoformat(),
        "execution_results": results,
        "execution_errors": errors
    })
    
    return {
        "results": results,
        "errors": errors,
        "main_command_result": next((r["result"] for r in results if not r["is_required"]), None)
    }

def resume_stack_execution(user_id, command_engine):
    """Resume stack execution after data collection is complete"""
    from memory.data_collector import needs_more_input, receive_input
    
    # Check if we're still collecting data
    if needs_more_input(user_id):
        filled = receive_input(user_id, "")
        if filled:
            # Data collection complete, continue with stack execution
            return execute_complete_stack(user_id, command_engine)
        else:
            # Still need more input
            return {
                "needs_input": True,
                "message": "Still waiting for required information"
            }
    else:
        # No data collection in progress, execute the stack
        return execute_complete_stack(user_id, command_engine)