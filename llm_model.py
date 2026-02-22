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


def call_gpt(
    system_prompt: str,
    user_prompt: str,
    model: str = "gpt-4o",
    max_tokens: int = 4000
) -> str:
    """
    Call GPT with system and user prompts.

    Used by brain.py for the final trading decision call, which receives the
    full context: OHLC analysis + visual chart observations + market data +
    current positions + prior analysis notes.

    Args:
        system_prompt: System instructions (SOD or Intraday prompt)
        user_prompt:   Full trading context assembled by brain.py
        model:         OpenAI model to use (default gpt-4o for quality decisions)
        max_tokens:    Max response tokens (default 4000 for detailed decisions)

    Returns:
        Raw GPT response text (JSON string expected — parsed by brain.py)
    """
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt}
        ],
        max_tokens=max_tokens,
        temperature=0.3
    )
    return response.choices[0].message.content
