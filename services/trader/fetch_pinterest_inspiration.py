#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pinterest Design Inspiration Script
Fetches design trends (color themes, layout styles) from Pinterest to inspire card news styles.
"""

import os
import sys
import json
import requests
from pathlib import Path
from dotenv import load_dotenv

# Set up project root path
ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Load environment variables
load_dotenv(dotenv_path=ROOT_DIR / ".env")

OUTPUT_PATH = ROOT_DIR / "data" / "pinterest_inspiration.json"

def fetch_inspiration():
    token = os.getenv("PINTEREST_ACCESS_TOKEN")
    
    # Setup search query
    query = "editorial design layout orange sage green"
    print(f"Retrieving design inspiration for query: '{query}'...")
    
    inspiration_data = {
        "query": query,
        "colors": {
            "primary_orange": "#E85A24",
            "secondary_green": "#2A7F54",
            "background": "#F7F6F0",
            "accent_sage_light": "#EAF5EE"
        },
        "layouts": [
            {
                "style": "editorial_card_news",
                "font_title": "GmarketSansBold",
                "font_body": "Pretendard-Regular",
                "character_placement": "bottom_right_cropped",
                "opacity": 0.15
            }
        ],
        "source": "Pinterest Design Board"
    }
    
    if not token:
        print("⚠️ PINTEREST_ACCESS_TOKEN is missing in `.env`.")
        print("⚠️ Saving default premium Pinterest template presets...")
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump(inspiration_data, f, indent=2, ensure_ascii=False)
        return False
        
    print("Connecting to Pinterest API...")
    headers = {
        "Authorization": f"Bearer {token}"
    }
    
    # We query the Pinterest Search API for pins matching the layout style
    search_url = f"https://api.pinterest.com/v5/search/pins?query={requests.utils.quote(query)}&limit=5"
    
    try:
        res = requests.get(search_url, headers=headers, timeout=30)
        res.raise_for_status()
        pins = res.json().get("items", [])
        
        pins_list = []
        for pin in pins:
            pins_list.append({
                "id": pin.get("id"),
                "title": pin.get("title"),
                "description": pin.get("description"),
                "image_url": pin.get("media", {}).get("images", {}).get("originals", {}).get("url")
            })
            
        inspiration_data["pins"] = pins_list
        print(f"✅ Successfully retrieved {len(pins_list)} design references from Pinterest.")
    except Exception as e:
        print(f"⚠️ Failed to call Pinterest API: {e}. Falling back to default design presets.")
        
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(inspiration_data, f, indent=2, ensure_ascii=False)
        
    print(f"Saved Pinterest design inspiration to {OUTPUT_PATH}")
    return True

if __name__ == "__main__":
    fetch_inspiration()
