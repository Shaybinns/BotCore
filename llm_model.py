"""
LLM Model Integration

Handles OpenAI API calls for text-only analysis (trading decisions).
Chart image analysis is handled separately in chart_analyzer.py via the Vision API.
"""

from openai import OpenAI
import os
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Default model — chat, commands, classifier, simple tasks
DEFAULT_MODEL = "gpt-5.4-nano"

# Mini model — synthesis, analysis, trading decisions, deeper reasoning
MINI_MODEL = "gpt-5.4-mini"


def call_gpt(
    system_prompt: str,
    user_prompt: str,
    model: str = DEFAULT_MODEL,
    max_completion_tokens: int = 6000,
    temperature: float = 0.7,
) -> str:
    """Standard call — gpt-5.4-nano. For chat, commands, and simple tasks."""
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_completion_tokens=max_completion_tokens,
        temperature=temperature,
    )
    return response.choices[0].message.content


def call_gpt_mini(
    system_prompt: str,
    user_prompt: str,
    model: str = MINI_MODEL,
    max_completion_tokens: int = 6000,
    temperature: float = 0.7,
) -> str:
    """Mini call — gpt-5.4-mini. For synthesis, portfolio analysis, and trading decisions."""
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_completion_tokens=max_completion_tokens,
        temperature=temperature,
    )
    return response.choices[0].message.content
