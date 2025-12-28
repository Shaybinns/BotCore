import requests
import json
from datetime import datetime
import os

def get_required_fields():
    return {
        "query": {"prompt": "What would you like to search the internet for?"}
    }

def run(args: dict):
    query = args["query"]
    current_time = datetime.now().strftime("%A, %B %d, %Y at %H:%M %p")

    prompt = f"""You are a web search agent. The current date and time is {current_time}.
Use the internet to find the most relevant, up-to-date answer to the following question:

{query}
"""

    headers = {
        "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
        "Content-Type": "application/json",
    }

    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers=headers,
        json={
            "model": "perplexity/sonar-pro",
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 1000
        }
    )

    if response.status_code != 200:
        raise Exception(f"API Error: {response.status_code} — {response.text}")

    data = response.json()
    return data['choices'][0]['message']['content']
