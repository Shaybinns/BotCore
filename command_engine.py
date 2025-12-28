import json

def run_command(command_name: str, args: dict = {}):
    try:
        # Dynamically import from commands folder
        module = __import__(f"commands.{command_name}", fromlist=["run"])
        result = module.run(args)
        return result
    except Exception as e:
        return f"[Command Error: {str(e)}]"
