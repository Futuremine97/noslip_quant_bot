#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Figma Mascot Downloader Script
Fetches different poses of the mascot character '귤이' (Tangerine) from Figma.
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

OUTPUT_DIR = ROOT_DIR / "data" / "mascot_poses"

def fetch_mascot_poses():
    token = os.getenv("FIGMA_PERSONAL_ACCESS_TOKEN")
    file_id = os.getenv("FIGMA_FILE_ID")
    
    if not token or not file_id:
        print("⚠️ FIGMA_PERSONAL_ACCESS_TOKEN or FIGMA_FILE_ID is missing in `.env`.")
        print("⚠️ Skipping Figma fetch. Pre-existing local assets or fallback images will be used.")
        return False
        
    print(f"Connecting to Figma File ID: {file_id}...")
    headers = {
        "X-Figma-Token": token
    }
    
    # 1. Get file content to find mascot components/frames
    file_url = f"https://api.figma.com/v1/files/{file_id}"
    try:
        res = requests.get(file_url, headers=headers, timeout=30)
        res.raise_for_status()
        file_data = res.json()
    except Exception as e:
        print(f"❌ Failed to fetch Figma file structure: {e}")
        return False
        
    # Helper to traverse node tree and find mascot nodes
    mascot_nodes = {}
    
    def traverse(node):
        node_id = node.get("id")
        name = node.get("name", "")
        # Find components, frames or vector instances containing 'mascot' or 'gyul' in name
        if "mascot" in name.lower() or "gyul" in name.lower() or "귤이" in name:
            # Clean name for filename
            clean_name = name.replace(" ", "_").replace("/", "_").replace("\\", "_")
            mascot_nodes[node_id] = clean_name
            
        if "children" in node:
            for child in node["children"]:
                traverse(child)
                
    traverse(file_data.get("document", {}))
    
    if not mascot_nodes:
        print("⚠️ No components or nodes matching 'mascot' or '귤이' found in the Figma file.")
        return False
        
    print(f"Found {len(mascot_nodes)} mascot nodes: {list(mascot_nodes.values())}")
    
    # 2. Get rendering URLs for these nodes
    ids_param = ",".join(mascot_nodes.keys())
    image_url = f"https://api.figma.com/v1/images/{file_id}?ids={ids_param}&format=png&scale=2"
    
    try:
        res = requests.get(image_url, headers=headers, timeout=30)
        res.raise_for_status()
        images_data = res.json().get("images", {})
    except Exception as e:
        print(f"❌ Failed to request Figma node rendering: {e}")
        return False
        
    # 3. Download the rendered PNG images
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    success_count = 0
    
    for node_id, img_url in images_data.items():
        if not img_url:
            continue
        node_name = mascot_nodes.get(node_id, f"mascot_{node_id}")
        dest_path = OUTPUT_DIR / f"{node_name}.png"
        
        try:
            print(f"Downloading {node_name} -> {dest_path}...")
            img_res = requests.get(img_url, timeout=30)
            img_res.raise_for_status()
            with open(dest_path, "wb") as f:
                f.write(img_res.content)
            success_count += 1
        except Exception as e:
            print(f"⚠️ Failed to download mascot image for {node_name}: {e}")
            
    print(f"✅ Successfully downloaded {success_count} mascot poses from Figma to {OUTPUT_DIR}")
    return True

if __name__ == "__main__":
    fetch_mascot_poses()
