# utils/field_extraction.py

import re
import json
from llm_model import call_gpt
from prompt import get_plugin_system_prompt

def extract_fields_from_text(message: str, required_fields: dict) -> dict:
    """
    Use GPT to extract multiple fields from a user message.
    `required_fields` should be a dict with field names as keys.
    """
    field_list = ', '.join(required_fields.keys())

    # Use GPT to extract fields if regex fails
    plugin_system_prompt = get_plugin_system_prompt()
    system_prompt = f"{plugin_system_prompt}\n\nYou are Portfolio AI's field extraction specialist. Your role is to extract specific data fields from user input accurately and efficiently."
    
    prompt = f"""
Extract the following fields from this user input: {', '.join(required_fields)}

User input: "{message}"

Return ONLY a valid JSON object with the extracted fields. If a field cannot be determined, use null.
Example format: {{"field1": "value1", "field2": "value2", "field3": null}}
"""

    response = call_gpt(system_prompt, prompt)

    try:
        return json.loads(response)
    except Exception:
        return {}
