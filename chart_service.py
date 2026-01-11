"""
Chart Service - Chart-IMG.com Integration

Fetches chart images from chart-img.com for visual analysis.
"""

import requests
import os
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

CHART_IMG_API_KEY = os.getenv("CHART_IMG_API_KEY")
CHART_IMG_BASE_URL = "https://api.chart-img.com/v1/tradingview/advanced-chart"


def get_chart_image(
    symbol: str,
    timeframe: str = "H1",
    session: Optional[str] = None,
    width: int = 1200,
    height: int = 800
) -> Optional[str]:
    """
    Get chart image URL from chart-img.com.
    
    Args:
        symbol: Trading symbol (e.g., "EURUSD")
        timeframe: Chart timeframe (H1, M15, M5, M1, etc.)
        session: Session identifier (London, NY, etc.) - used for context
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
            "D1": "D"
        }
        
        chart_timeframe = timeframe_map.get(timeframe, "60")
        
        # Build request
        params = {
            "symbol": symbol,
            "interval": chart_timeframe,
            "width": width,
            "height": height,
            "theme": "dark",  # Dark theme for better contrast
            "studies": ""  # No indicators for clean chart
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
            # Chart-img typically returns image URL or base64
            if "image" in data:
                return data["image"]
            elif "url" in data:
                return data["url"]
            else:
                # If it's base64, we might need to handle it differently
                return data.get("data", None)
        else:
            print(f"Chart-img API error: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        print(f"Error fetching chart image: {e}")
        return None

