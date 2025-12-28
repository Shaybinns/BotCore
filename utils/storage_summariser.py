# utils/storage_summariser.py

from llm_model import call_gpt
from prompt import get_plugin_system_prompt

def summarise_result(command_name: str, raw_result: str) -> str:
    plugin_system_prompt = get_plugin_system_prompt()
    system_prompt = f"{plugin_system_prompt}\n\nYou are Portfolio AI's storage summarizer. Your role is to create brief, structured summaries for data storage and retrieval."
    
    prompt = f"""
You are a summarisation engine. Summarise the output of a completed command.

Command: {command_name}

Raw Result:
{raw_result}

Your job is to:
- Return a 1–2 sentence summary
- Focus on the useful insight or action completed
- Do NOT repeat the entire raw result

Example:
Command: create_portfolio
Raw Result: "Portfolio created with 40% equities, 40% bonds, and 20% gold..."
Summary: "Created a diversified portfolio with equities, bonds, and gold."
"""

    return call_gpt(system_prompt, prompt)
