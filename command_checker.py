import re
import json

def extract_command_from_text(text: str):
    match = re.search(r"#COMMAND\s+(\w+)(.*)", text)
    if not match:
        return None, None

    command_name = match.group(1)
    args_raw = match.group(2).strip()

    try:
        args = json.loads(args_raw) if args_raw else {}
    except:
        args = {}

    return command_name, args
