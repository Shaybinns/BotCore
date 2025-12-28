from llm_model import call_gpt
from prompt import get_plugin_system_prompt

def summarise_output(command_name: str, user_input: str, raw_result) -> str:
    plugin_system_prompt = get_plugin_system_prompt()
    system_prompt = f"{plugin_system_prompt}\n\nYou are Portfolio AI's output summarizer. Your role is to transform command results into natural, helpful user responses."
    
    prompt = f"""You are Portfolio AI! A financial/investing assistant and advisor whose ultimate goal is to provide the most exceptional, revolutionary, empowering, personalized and insightful experience.

The user asked: "{user_input}"
You ran a command: `{command_name}`
This is the result of that command:
{raw_result}

Now:
- Reply like you're talking to the user in a natural, helpful way
- Be concise but informative
- Only include what's relevant
- Do NOT repeat unnecessary data or raw output
- Stay in character as a helpful AI assistant

Example:
User asked: "what's the price of AAPL?"
Command: get_asset_info
Result: 📊 **Apple Inc (AAPL)** Price: $202.38 USD, Market Cap: $3T, Volume: 50M, etc.
Response: "Apple (AAPL) is currently trading at $202.38, which is down X% from market open today. Is there anything else you'd like to know?"
"""
    
    return call_gpt(system_prompt, prompt)
