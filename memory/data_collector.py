from utils.field_extraction import extract_fields_from_text

waiting_inputs = {}  # user_id → {command, args, missing_fields, field_meta}

def needs_more_input(user_id):
    return user_id in waiting_inputs

def start_data_collection(user_id, command_name, args, missing_fields, required_fields):
    waiting_inputs[user_id] = {
        "command": command_name,
        "args": args,
        "missing": missing_fields,
        "field_meta": required_fields
    }

def receive_input(user_id, user_message):
    if user_id not in waiting_inputs:
        return None

    entry = waiting_inputs[user_id]
    args = entry["args"]
    required_fields = entry["field_meta"]
    missing = entry["missing"]

    # 🔍 Use GPT to extract available fields from message
    extracted = extract_fields_from_text(user_message, required_fields)

    for key, value in extracted.items():
        if key in missing:
            args[key] = value
            missing.remove(key)

    # ✅ If all fields filled, return full command
    if not missing:
        return waiting_inputs.pop(user_id)

    return None  # Still collecting