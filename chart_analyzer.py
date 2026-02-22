"""
Chart Analyzer

Fetches chart images and analyzes them using GPT Vision.
Integrates chart-img.com API and OpenAI Vision API.
"""

import requests
import os
import json
import base64
from typing import Dict, Any, Optional, List
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

CHART_IMG_API_KEY = os.getenv("CHART_IMG_API_KEY")
CHART_IMG_BASE_URL = "https://api.chart-img.com/v1/tradingview/advanced-chart"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


def get_chart_image(
    symbol: str,
    timeframe: str = "H1",
    width: int = 1200,
    height: int = 800
) -> Optional[str]:
    """
    Get chart image URL from chart-img.com.
    
    Args:
        symbol: Trading symbol (e.g., "EURUSD")
        timeframe: Chart timeframe (H1, H4, D1, W1, etc.)
        width: Image width in pixels
        height: Image height in pixels
    
    Returns:
        Chart image URL or None if failed
    """
    if not CHART_IMG_API_KEY:
        print("Warning: CHART_IMG_API_KEY not set")
        return None
    
    try:
        # Map timeframe to chart-img format
        timeframe_map = {
            "M1": "1",
            "M5": "5",
            "M15": "15",
            "M30": "30",
            "H1": "60",
            "H4": "240",
            "D1": "D",
            "W1": "W"
        }
        
        chart_timeframe = timeframe_map.get(timeframe, "60")
        
        # Build request
        params = {
            "symbol": symbol,
            "interval": chart_timeframe,
            "width": width,
            "height": height,
            "theme": "dark",
            "studies": ""
        }
        
        headers = {
            "X-RapidAPI-Key": CHART_IMG_API_KEY,
            "X-RapidAPI-Host": "chart-img.com"
        }
        
        # Make request
        response = requests.get(
            CHART_IMG_BASE_URL,
            params=params,
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            if "image" in data:
                return data["image"]
            elif "url" in data:
                return data["url"]
            else:
                return data.get("data", None)
        else:
            print(f"Chart-img API error: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        print(f"Error fetching chart image: {e}")
        return None


def analyze_charts_with_gpt_vision(
    symbol: str,
    timeframes: List[str],
    context: str,
    system_prompt: str
) -> Dict[str, Any]:
    """
    Fetch chart images for multiple timeframes and analyze with GPT Vision.
    
    Args:
        symbol: Trading symbol
        timeframes: List of timeframes to analyze (e.g., ["H1", "H4", "D1", "W1"])
        context: Text context to include with images (OHLC data, market data, etc.)
        system_prompt: System prompt for GPT Vision
    
    Returns:
        GPT Vision analysis result as dictionary
    """
    if not client:
        raise ValueError("OPENAI_API_KEY not set")
    
    # Fetch all chart images
    chart_images = []
    for tf in timeframes:
        print(f"Fetching {symbol} {tf} chart...")
        image_url = get_chart_image(symbol, tf)
        if image_url:
            chart_images.append({
                "timeframe": tf,
                "url": image_url
            })
        else:
            print(f"Warning: Failed to fetch {tf} chart")
    
    if not chart_images:
        print("No chart images fetched, falling back to text-only analysis")
        return _call_text_only(context, system_prompt)
    
    # Fetch images and convert to base64
    image_contents = []
    for chart in chart_images:
        image_base64 = _fetch_image_as_base64(chart["url"])
        if image_base64:
            image_contents.append({
                "timeframe": chart["timeframe"],
                "base64": image_base64
            })
    
    if not image_contents:
        print("Failed to fetch image data, falling back to text-only")
        return _call_text_only(context, system_prompt)
    
    # Build messages for vision API with multiple images
    content = [{"type": "text", "text": context}]
    
    for img in image_contents:
        content.append({
            "type": "text",
            "text": f"\nChart for {img['timeframe']} timeframe:"
        })
        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{img['base64']}"
            }
        })
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content}
    ]
    
    # Call OpenAI Vision API
    try:
        response = client.chat.completions.create(
            model="gpt-4o",  # Vision-capable model
            messages=messages,
            max_tokens=4000,
            temperature=0.3
        )
        
        content = response.choices[0].message.content
        return _parse_gpt_response(content)
        
    except Exception as e:
        print(f"Error calling GPT Vision: {e}")
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
