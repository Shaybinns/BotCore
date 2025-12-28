from prompt import get_system_prompt
from memory.short_term_cache import get_recent_conversation, add_to_recent_conversation
from memory.command_stack import (
    peek_stack, has_pending_steps,
    build_command_stack_with_dependencies, execute_complete_stack,
    resume_stack_execution
)
from command_engine import run_command
from memory.long_term_db import save_result, get_user_facts
from memory.knowledge_memory import get_vector_matches
from llm_model import call_gpt
from command_checker import extract_command_from_text
from memory.data_collector import (
    needs_more_input, receive_input, start_data_collection
)
from utils.storage_summariser import summarise_result
from utils.output_summariser import summarise_output



def handle_user_message(user_id: str, message: str) -> dict:
    # STEP 1: Handle pending input collection
    if needs_more_input(user_id):
        filled = receive_input(user_id, message)

        if filled:
            # Check if we're in a command stack execution
            
            if has_pending_steps(user_id):
                # Resume stack execution
                execution_result = resume_stack_execution(user_id, run_command)
                
                if execution_result.get("needs_input"):
                    # Still need more input
                    missing_fields = execution_result.get("missing_fields", [])
                    current_command = execution_result.get("current_command", "unknown")
                    prompts = [f"Please provide: {field}" for field in missing_fields]
                    combined_prompt = "\n".join(prompts)
                    reply = f"Thanks! Now I need a few more things for {current_command}:\n{combined_prompt}"
                else:
                    # Stack execution complete
                    main_result = execution_result.get("main_command_result")
                    if main_result:
                        output = summarise_output(filled["command"], message, main_result)
                        reply = f"Thanks! I've got everything I need.\n\n{output}"
                    else:
                        reply = f"Thanks! I've got everything I need. Command executed successfully."
            else:
                # Regular single command execution
                try:
                    result = run_command(filled["command"], filled["args"])
                    summary = summarise_result(filled["command"], result)
                    save_result(user_id, summary)
                    output = summarise_output(filled["command"], message, result)
                    reply = f"Thanks! I've got everything I need.\n\n{output}"
                except Exception as e:
                    reply = f"[Error running command]: {str(e)}"
        else:
            reply = f"Got it. What's the next input I need?"

        add_to_recent_conversation(user_id, f"User: {message}")
        add_to_recent_conversation(user_id, f"Assistant: {reply}")
        return {
            "initial_response": reply,
            "command_result": None,
            "command_executed": False,
            "status": "input_collection"
        }

    # STEP 2: Task reminder if stack exists
    task_reminder = ""
    if has_pending_steps(user_id):
        current = peek_stack(user_id)
        task_reminder = f"(You're currently in a multi-step task — next step is {current['command']}.)"

    # STEP 3: Build full GPT context
    system_prompt = get_system_prompt(user_id)
    recent_chat = get_recent_conversation(user_id)
    user_facts = get_user_facts(user_id)
    vector_recall = get_vector_matches(message)

    context = f"""
[User Facts]
{user_facts}

[Relevant Knowledge]
{vector_recall}

[Conversation So Far]
{recent_chat}

User: {message}
{task_reminder}
"""
    reply = call_gpt(system_prompt, context)

    # STEP 4: Extract goal from reply
    goal = None
    for line in reply.splitlines():
        if line.lower().startswith("goal:") or line.lower().startswith("task:"):
            goal = line.split(":", 1)[1].strip()
            break

    # STEP 4: Command detection + execution logic
    command_name, args = extract_command_from_text(reply)
    if command_name:
        # Send the initial AI response immediately
        add_to_recent_conversation(user_id, f"User: {message}")
        add_to_recent_conversation(user_id, f"Assistant: {reply}")
        
        try:
            # Check for structured field metadata
            try:
                module = __import__(f"commands.{command_name}", fromlist=["get_required_fields"])
                required_fields = module.get_required_fields()

                if isinstance(required_fields, dict):
                    missing_fields = [field for field in required_fields if field not in args]
                else:
                    missing_fields = [field for field in required_fields if field not in args]
                    required_fields = {field: {"prompt": f"Please provide: {field}"} for field in required_fields}

                if missing_fields:
                    prompts = [
                        f"{idx + 1}. {required_fields[field].get('prompt', f'Please provide: {field}')}"
                        for idx, field in enumerate(missing_fields)
                    ]
                    combined_prompt = "\n".join(prompts)
                    start_data_collection(user_id, command_name, args, missing_fields, required_fields)
                    follow_up = f"\n\nTo run {command_name}, I need a few things:\n{combined_prompt}"
                    add_to_recent_conversation(user_id, f"Assistant: {follow_up}")
                    return {
                        "initial_response": reply,
                        "command_result": follow_up,
                        "command_executed": False,
                        "status": "needs_input",
                        "command_name": command_name,
                        "missing_fields": missing_fields
                    }
            except (ImportError, AttributeError):
                pass

            # Check if this command has dependencies that need to be built into a stack
            from memory.command_stack import get_required_commands
            required_commands = get_required_commands(command_name)
            
            if required_commands:
                # Add user_id to args for command stack
                args["user_id"] = user_id
                # Build complete command stack with dependencies
                build_command_stack_with_dependencies(user_id, command_name, args, goal=goal)
                
                # Execute the complete stack
                execution_result = execute_complete_stack(user_id, run_command)
                
                # Check if we need user input
                if execution_result.get("needs_input"):
                    missing_fields = execution_result.get("missing_fields", [])
                    current_command = execution_result.get("current_command", command_name)
                    
                    # Create prompts for missing fields
                    prompts = [f"Please provide: {field}" for field in missing_fields]
                    combined_prompt = "\n".join(prompts)
                    
                    follow_up = f"\n\nTo run {current_command}, I need a few things:\n{combined_prompt}"
                    add_to_recent_conversation(user_id, f"Assistant: {follow_up}")
                    
                    return {
                        "initial_response": reply,
                        "command_result": follow_up,
                        "command_executed": False,
                        "status": "needs_input",
                        "command_name": current_command,
                        "missing_fields": missing_fields,
                        "stack_executed": True
                    }
                
                # Get the main command result (not the required commands)
                main_result = execution_result["main_command_result"]
                
                if main_result:
                    output = summarise_output(command_name, message, main_result)
                    follow_up = f"[Task Complete]\n{output}"
                else:
                    follow_up = f"[Task Complete] Command executed successfully."
                
                add_to_recent_conversation(user_id, f"Assistant: {follow_up}")
                return {
                    "initial_response": reply,
                    "command_result": follow_up,
                    "command_executed": True,
                    "status": "command_complete",
                    "command_name": command_name,
                    "goal": goal,
                    "stack_executed": True
                }
            else:
                # Simple command without dependencies - execute normally
                result = run_command(command_name, args)
                summary = summarise_result(command_name, result)
                save_result(user_id, summary)
                output = summarise_output(command_name, message, result)

                follow_up = f"[Task Complete]\n{output}"
                add_to_recent_conversation(user_id, f"Assistant: {follow_up}")
                return {
                    "initial_response": reply,
                    "command_result": follow_up,
                    "command_executed": True,
                    "status": "command_complete",
                    "command_name": command_name,
                    "goal": goal
                }

        except Exception as e:
            error_msg = f"Command execution failed: {str(e)}"
            follow_up = f"[Error]: {error_msg}"
            add_to_recent_conversation(user_id, f"Assistant: {follow_up}")
            return {
                "initial_response": reply,
                "command_result": follow_up,
                "command_executed": False,
                "status": "error",
                "error": str(e),
                "command_name": command_name
            }

    # STEP 5: Regular response (no command)
    add_to_recent_conversation(user_id, f"User: {message}")
    add_to_recent_conversation(user_id, f"Assistant: {reply}")
    return {
        "initial_response": reply,
        "command_result": None,
        "command_executed": False,
        "status": "conversation_only"
    }

def generate_ai_response_only(user_id: str, message: str) -> str:
    """Generate only the AI's initial response without executing commands"""
    # STEP 1: Handle pending input collection
    if needs_more_input(user_id):
        return "I need more information to proceed. What would you like me to do?"
    
    # STEP 2: Task reminder if stack exists
    task_reminder = ""
    if has_pending_steps(user_id):
        current = peek_stack(user_id)
        task_reminder = f"(You're currently in a multi-step task — next step is {current['command']}.)"
    
    # STEP 3: Build full GPT context
    system_prompt = get_system_prompt(user_id)
    recent_chat = get_recent_conversation(user_id)
    user_facts = get_user_facts(user_id)
    vector_recall = get_vector_matches(message)
    
    context = f"""
[User Facts]
{user_facts}

[Relevant Knowledge]
{vector_recall}

[Conversation So Far]
{recent_chat}

You are replying directly to the user's message, which is - User: {message}
{task_reminder}
"""
    reply = call_gpt(system_prompt, context)
    
    # STEP 4: Extract goal from reply
    goal = None
    for line in reply.splitlines():
        if line.lower().startswith("goal:") or line.lower().startswith("task:"):
            goal = line.split(":", 1)[1].strip()
            break
    
    # STEP 5: Command detection (but don't execute)
    command_name, args = extract_command_from_text(reply)
    
    if command_name:
        # Add conversation to memory
        add_to_recent_conversation(user_id, f"User: {message}")
        add_to_recent_conversation(user_id, f"Assistant: {reply}")
        
        # Return the AI's response and detected command info
        return reply, command_name, args, goal
    else:
        # No command, just conversation
        add_to_recent_conversation(user_id, f"User: {message}")
        add_to_recent_conversation(user_id, f"Assistant: {reply}")
        return reply, None, None, None

def execute_command_streaming(command_name: str, args: dict, user_id: str, message: str) -> dict:
    """Execute a command and return results for streaming"""
    try:
        # Add user_id to args for command stack
        args["user_id"] = user_id
        # Check for structured field metadata
        try:
            module = __import__(f"commands.{command_name}", fromlist=["get_required_fields"])
            required_fields = module.get_required_fields()
            
            if isinstance(required_fields, dict):
                missing_fields = [field for field in required_fields if field not in args]
            else:
                missing_fields = [field for field in required_fields if field not in args]
                required_fields = {field: {"prompt": f"Please provide: {field}"} for field in required_fields}
            
            if missing_fields:
                prompts = [
                    f"{idx + 1}. {required_fields[field].get('prompt', f'Please provide: {field}')}"
                    for idx, field in enumerate(missing_fields)
                ]
                combined_prompt = "\n".join(prompts)
                start_data_collection(user_id, command_name, args, missing_fields, required_fields)
                follow_up = f"\n\nTo run {command_name}, I need a few things:\n{combined_prompt}"
                add_to_recent_conversation(user_id, f"Assistant: {follow_up}")
                return {
                    "command_result": follow_up,
                    "command_executed": False,
                    "status": "needs_input",
                    "command_name": command_name,
                    "missing_fields": missing_fields
                }
        except (ImportError, AttributeError):
            pass
        
        # Check if this command has dependencies that need to be built into a stack
        from memory.command_stack import get_required_commands
        required_commands = get_required_commands(command_name)
        
        if required_commands:
            # Build complete command stack with dependencies
            build_command_stack_with_dependencies(user_id, command_name, args, goal=None)
            
            # Execute the complete stack
            execution_result = execute_complete_stack(user_id, run_command)
            
            # Get the main command result (not the required commands)
            main_result = execution_result["main_command_result"]
            
            if main_result:
                output = summarise_output(command_name, message, main_result)
                follow_up = f"[Task Complete]\n{output}"
            else:
                follow_up = f"[Task Complete] Command executed successfully."
            
            add_to_recent_conversation(user_id, f"Assistant: {follow_up}")
            return {
                "command_result": follow_up,
                "command_executed": True,
                "status": "command_complete",
                "command_name": command_name,
                "stack_executed": True
            }
        else:
            # Simple command without dependencies - execute normally
            result = run_command(command_name, args)
            summary = summarise_result(command_name, result)
            save_result(user_id, summary)
            output = summarise_output(command_name, message, result)
            
            follow_up = f"[Task Complete]\n{output}"
            add_to_recent_conversation(user_id, f"Assistant: {follow_up}")
            return {
                "command_result": follow_up,
                "command_executed": True,
                "status": "command_complete",
                "command_name": command_name
            }
        
    except Exception as e:
        error_msg = f"Command execution failed: {str(e)}"
        follow_up = f"[Error]: {error_msg}"
        add_to_recent_conversation(user_id, f"Assistant: {follow_up}")
        return {
            "command_result": follow_up,
            "command_executed": False,
            "status": "error",
            "error": str(e),
            "command_name": command_name
        }