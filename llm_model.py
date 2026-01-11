"""
LLM Model Integration

Handles OpenAI API calls for text-only analysis.
"""

from openai import OpenAI
import os
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def call_gpt(system_prompt: str, user_prompt: str) -> str:
    """
    Call GPT with system and user prompts.
    
    Args:
        system_prompt: System instructions
        user_prompt: User context/query
    
    Returns:
        GPT response text
    """
    response = client.chat.completions.create(
        model="gpt-4o-mini",  # Use mini for text-only, gpt-4o for vision
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        max_tokens=2000,
        temperature=0.3  # Lower temperature for consistent trading decisions
    )
    return response.choices[0].message.content
