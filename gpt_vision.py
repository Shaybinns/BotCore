"""
GPT Vision Integration

Handles chart image analysis using OpenAI's vision capabilities.
"""

import os
import json
import base64
import requests
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from prompt import get_trading_prompt

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"


def analyze_chart_with_gpt(
    context: str,
    chart_image_url: str
) -> Dict[str, Any]:
    """
    Analyze chart image using GPT-4 Vision.
    
    Args:
        context: Text context (OHLC data, levels, etc.)
        chart_image_url: URL to chart image
    
    Returns:
        Trading decision JSON
    """
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY not set")
    
    system_prompt = get_trading_prompt()
    
    # Fetch image and convert to base64
    image_base64 = _fetch_image_as_base64(chart_image_url)
    if not image_base64:
        # Fallback to text-only if image fetch fails
        return _call_text_only(context, system_prompt)
    
    # Build messages for vision API
    messages = [
        {
            "role": "system",
            "content": system_prompt
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": context
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{image_base64}"
                    }
                }
            ]
        }
    ]
    
    # Call OpenAI Vision API
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "gpt-4o",  # Use vision-capable model
        "messages": messages,
        "max_tokens": 2000,
        "temperature": 0.3  # Lower temperature for more consistent trading decisions
    }
    
    try:
        response = requests.post(
            OPENAI_API_URL,
            headers=headers,
            json=payload,
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            content = result["choices"][0]["message"]["content"]
            
            # Parse JSON from response
            return _parse_gpt_response(content)
        else:
            print(f"OpenAI API error: {response.status_code} - {response.text}")
            # Fallback to text-only
            return _call_text_only(context, system_prompt)
            
    except Exception as e:
        print(f"Error calling GPT Vision: {e}")
        # Fallback to text-only
        return _call_text_only(context, system_prompt)


def _fetch_image_as_base64(image_url: str) -> Optional[str]:
    """Fetch image from URL and convert to base64."""
    try:
        response = requests.get(image_url, timeout=10)
        if response.status_code == 200:
            image_bytes = response.content
            return base64.b64encode(image_bytes).decode('utf-8')
        return None
    except Exception as e:
        print(f"Error fetching image: {e}")
        return None


def _call_text_only(context: str, system_prompt: str) -> Dict[str, Any]:
    """Fallback to text-only GPT call."""
    from llm_model import call_gpt
    
    response = call_gpt(system_prompt, context)
    return _parse_gpt_response(response)


def _parse_gpt_response(response_text: str) -> Dict[str, Any]:
    """Parse JSON from GPT response."""
    try:
        # Extract JSON from response if wrapped in markdown
        if "```json" in response_text:
            json_start = response_text.find("```json") + 7
            json_end = response_text.find("```", json_start)
            json_str = response_text[json_start:json_end].strip()
        elif "```" in response_text:
            json_start = response_text.find("```") + 3
            json_end = response_text.find("```", json_start)
            json_str = response_text[json_start:json_end].strip()
        else:
            json_str = response_text.strip()
        
        # Try to find JSON object
        import re
        json_match = re.search(r'\{.*\}', json_str, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        
        # If no match, try parsing the whole string
        return json.loads(json_str)
        
    except json.JSONDecodeError as e:
        print(f"Error parsing GPT response: {e}")
        print(f"Response text: {response_text[:500]}")
        # Return safe default
        return {
            "action": "WAIT",
            "next_run_at_utc": None,
            "error": "Failed to parse AI response"
        }

