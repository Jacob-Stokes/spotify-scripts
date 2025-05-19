#!/usr/bin/env python3
"""
Spotify Dynamic Playlist Updater

This script:
1. Changes playlist cover art at set intervals
2. Changes playlist title at set intervals  
3. Changes playlist description at set intervals
4. Each feature can be enabled/disabled independently
5. Runs continuously, checking at specified intervals
"""

import os
import time
import json
import datetime
import random
import requests
import base64
from pathlib import Path
from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyOAuth

# Load environment variables from parent directory .env file    
parent_dir = Path(__file__).parent.parent
env_path = parent_dir / '.env'
load_dotenv(dotenv_path=env_path)

# ====== CONFIGURATION ======
# Spotify API settings
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:8888/callback")
SPOTIFY_SCOPE = os.getenv("SPOTIFY_SCOPE", "playlist-modify-public playlist-modify-private ugc-image-upload")

# Target playlist
TARGET_PLAYLIST_ID = os.getenv("DYNAMIC_PLAYLIST_ID")

# Feature toggles
ENABLE_COVER_CHANGES = os.getenv("ENABLE_COVER_CHANGES", "false").lower() == "true"
ENABLE_TITLE_CHANGES = os.getenv("ENABLE_TITLE_CHANGES", "false").lower() == "true"
ENABLE_DESCRIPTION_CHANGES = os.getenv("ENABLE_DESCRIPTION_CHANGES", "false").lower() == "true"

# Sequential vs Random selection
COVER_SELECTION_MODE = os.getenv("COVER_SELECTION_MODE", "random").lower()  # "random" or "sequential"
TITLE_SELECTION_MODE = os.getenv("TITLE_SELECTION_MODE", "random").lower()  # "random" or "sequential"
DESCRIPTION_SELECTION_MODE = os.getenv("DESCRIPTION_SELECTION_MODE", "random").lower()  # "random" or "sequential"

# Intervals (in seconds)
COVER_CHANGE_INTERVAL = int(os.getenv("COVER_CHANGE_INTERVAL", "300"))  # 5 minutes default
TITLE_CHANGE_INTERVAL = int(os.getenv("TITLE_CHANGE_INTERVAL", "300"))  # 5 minutes default
DESCRIPTION_CHANGE_INTERVAL = int(os.getenv("DESCRIPTION_CHANGE_INTERVAL", "300"))  # 5 minutes default
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))  # How often to check if updates are needed

# Content sources
COVER_ART_DIR = os.getenv("COVER_ART_DIR", "cover_art")  # Directory with image files
TITLES_FILE = os.getenv("TITLES_FILE", "titles.txt")  # Text file with titles (one per line)
DESCRIPTIONS_FILE = os.getenv("DESCRIPTIONS_FILE", "descriptions.txt")  # Text file with descriptions
USE_DYNAMIC_DESCRIPTIONS = os.getenv("USE_DYNAMIC_DESCRIPTIONS", "true").lower() == "true"

# State tracking
STATE_FILE = os.getenv("DYNAMIC_PLAYLIST_STATE", "dynamic_playlist_state.json")

# Rate limiting
SPOTIFY_DELAY = float(os.getenv("SPOTIFY_API_DELAY", "0.2"))

# Fail early if required env vars are missing
required_vars = [
    "SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "DYNAMIC_PLAYLIST_ID"
]

missing_vars = [var for var in required_vars if not os.getenv(var)]
if missing_vars:
    raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

# ====== SPOTIFY AUTH ======
sp_oauth = SpotifyOAuth(
    scope=SPOTIFY_SCOPE,
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET,
    redirect_uri=SPOTIFY_REDIRECT_URI
)

sp = spotipy.Spotify(auth_manager=sp_oauth)

# ====== STATE MANAGEMENT ======
def load_state():
    """Load state from the state file"""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {
        "last_cover_change": None,
        "last_title_change": None,
        "last_description_change": None,
        "used_covers": [],
        "used_titles": [],
        "used_descriptions": [],
        "current_cover": None,
        "current_title": None,
        "current_description": None,
        # Sequential tracking
        "cover_index": 0,
        "title_index": 0,
        "description_index": 0
    }

def save_state(state):
    """Save state to the state file"""
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

# ====== CONTENT MANAGEMENT ======
def get_available_covers():
    """Get list of available cover art files in a consistent order"""
    cover_dir = Path(COVER_ART_DIR)
    if not cover_dir.exists():
        print(f"Cover art directory not found: {COVER_ART_DIR}")
        return []
    
    # Support common image formats
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp'}
    covers = []
    
    for file_path in cover_dir.iterdir():
        if file_path.is_file() and file_path.suffix.lower() in image_extensions:
            covers.append(str(file_path))
    
    # Sort for consistent ordering in sequential mode
    covers.sort()
    return covers

def get_available_titles():
    """Get list of available titles from file"""
    titles_file = Path(TITLES_FILE)
    if not titles_file.exists():
        return []
    
    with open(titles_file, 'r', encoding='utf-8') as f:
        titles = [line.strip() for line in f if line.strip()]
    
    # Titles are already in file order, good for sequential mode
    return titles

def get_available_descriptions():
    """Get list of available descriptions from file or generate dynamic ones"""
    if USE_DYNAMIC_DESCRIPTIONS:
        # Generate dynamic descriptions with current date/time
        now = datetime.datetime.now()
        current_time = now.strftime("%H:%M")
        current_date = now.strftime("%B %d, %Y")
        day_of_week = now.strftime("%A")
        
        dynamic_descriptions = [
            f"Updated at {current_time} on {day_of_week}",
            f"Current vibe as of {current_date}",
            f"Last refreshed: {current_time}",
            f"Today's mood: {day_of_week} energy",
            f"Live playlist - updated {current_time}",
            f"Fresh tracks for {day_of_week}",
            f"Curated on {current_date}",
            f"Real-time updates since {current_time}"
        ]
        return dynamic_descriptions
    
    descriptions_file = Path(DESCRIPTIONS_FILE)
    if not descriptions_file.exists():
        return []
    
    with open(descriptions_file, 'r', encoding='utf-8') as f:
        descriptions = [line.strip() for line in f if line.strip()]
    
    return descriptions

def select_item(available_items, used_items, item_type, selection_mode, state_key, state):
    """Select an item either randomly or sequentially"""
    if not available_items:
        print(f"No available {item_type} found")
        return None
    
    if selection_mode == "sequential":
        # Sequential selection
        current_index = state.get(state_key, 0)
        
        # Reset index if we've gone through all items
        if current_index >= len(available_items):
            current_index = 0
            print(f"Completed full cycle of {item_type}, starting over")
        
        selected = available_items[current_index]
        state[state_key] = current_index + 1
        
        print(f"Selected {item_type} {current_index + 1}/{len(available_items)}: {Path(selected).name if item_type == 'covers' else selected}")
        
    else:
        # Random selection (existing logic)
        # If we've used all items, reset the used list
        if len(used_items) >= len(available_items):
            print(f"Resetting used {item_type} list")
            used_items.clear()
        
        # Find items we haven't used yet
        unused_items = [item for item in available_items if item not in used_items]
        
        if not unused_items:
            # This shouldn't happen due to the reset above, but just in case
            unused_items = available_items
        
        selected = random.choice(unused_items)
        used_items.append(selected)
        
        print(f"Randomly selected {item_type}: {Path(selected).name if item_type == 'covers' else selected}")
    
    return selected

# ====== PLAYLIST UPDATE FUNCTIONS ======
def upload_cover_art(playlist_id, image_path):
    """Upload cover art to a playlist"""
    try:
        # Read and encode the image
        with open(image_path, 'rb') as image_file:
            image_data = image_file.read()
        
        # Spotify requires base64 encoded JPEG
        # If it's not a JPEG, you might need to convert it first
        encoded_image = base64.b64encode(image_data).decode('utf-8')
        
        time.sleep(SPOTIFY_DELAY)
        sp.playlist_upload_cover_image(playlist_id, encoded_image)
        print(f"Updated cover art with: {Path(image_path).name}")
        return True
    except Exception as e:
        print(f"Error uploading cover art {image_path}: {e}")
        return False

def update_playlist_title(playlist_id, new_title):
    """Update playlist title"""
    try:
        time.sleep(SPOTIFY_DELAY)
        sp.playlist_change_details(playlist_id, name=new_title)
        print(f"Updated title to: {new_title}")
        return True
    except Exception as e:
        print(f"Error updating title: {e}")
        return False

def update_playlist_description(playlist_id, new_description):
    """Update playlist description"""
    try:
        time.sleep(SPOTIFY_DELAY)
        sp.playlist_change_details(playlist_id, description=new_description)
        print(f"Updated description to: {new_description}")
        return True
    except Exception as e:
        print(f"Error updating description: {e}")
        return False

# ====== TIME CHECKING ======
def should_update(last_update_time, interval_seconds):
    """Check if enough time has passed since last update"""
    if not last_update_time:
        return True
    
    last_update = datetime.datetime.fromisoformat(last_update_time)
    time_since_update = datetime.datetime.now() - last_update
    
    return time_since_update.total_seconds() >= interval_seconds

# ====== MAIN UPDATE FUNCTIONS ======
def update_cover_if_needed(state):
    """Update cover art if interval has passed"""
    if not ENABLE_COVER_CHANGES:
        return
    
    if should_update(state.get('last_cover_change'), COVER_CHANGE_INTERVAL):
        covers = get_available_covers()
        if covers:
            new_cover = select_item(covers, state['used_covers'], 'covers', 
                                  COVER_SELECTION_MODE, 'cover_index', state)
            if new_cover and upload_cover_art(TARGET_PLAYLIST_ID, new_cover):
                state['last_cover_change'] = datetime.datetime.now().isoformat()
                state['current_cover'] = new_cover
                save_state(state)

def update_title_if_needed(state):
    """Update title if interval has passed"""
    if not ENABLE_TITLE_CHANGES:
        return
    
    if should_update(state.get('last_title_change'), TITLE_CHANGE_INTERVAL):
        titles = get_available_titles()
        if titles:
            new_title = select_item(titles, state['used_titles'], 'titles',
                                  TITLE_SELECTION_MODE, 'title_index', state)
            if new_title and update_playlist_title(TARGET_PLAYLIST_ID, new_title):
                state['last_title_change'] = datetime.datetime.now().isoformat()
                state['current_title'] = new_title
                save_state(state)

def update_description_if_needed(state):
    """Update description if interval has passed"""
    if not ENABLE_DESCRIPTION_CHANGES:
        return
    
    if should_update(state.get('last_description_change'), DESCRIPTION_CHANGE_INTERVAL):
        descriptions = get_available_descriptions()
        if descriptions:
            new_description = select_item(descriptions, state['used_descriptions'], 'descriptions',
                                        DESCRIPTION_SELECTION_MODE, 'description_index', state)
            if new_description and update_playlist_description(TARGET_PLAYLIST_ID, new_description):
                state['last_description_change'] = datetime.datetime.now().isoformat()
                state['current_description'] = new_description
                save_state(state)

# ====== MAIN FUNCTION ======
def run_dynamic_updater():
    """Main function to run the dynamic playlist updater"""
    state = load_state()
    
    print("\n" + "="*50)
    print("Spotify Dynamic Playlist Updater")
    print(f"Target Playlist: {TARGET_PLAYLIST_ID}")
    print(f"Features enabled:")
    print(f"  - Cover changes: {ENABLE_COVER_CHANGES} (every {COVER_CHANGE_INTERVAL}s, {COVER_SELECTION_MODE} mode)")
    print(f"  - Title changes: {ENABLE_TITLE_CHANGES} (every {TITLE_CHANGE_INTERVAL}s, {TITLE_SELECTION_MODE} mode)")
    print(f"  - Description changes: {ENABLE_DESCRIPTION_CHANGES} (every {DESCRIPTION_CHANGE_INTERVAL}s, {DESCRIPTION_SELECTION_MODE} mode)")
    print(f"Check interval: {CHECK_INTERVAL} seconds")
    print("="*50)
    
    try:
        while True:
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[{now}] Checking for updates...")
            
            # Check each feature
            update_cover_if_needed(state)
            update_title_if_needed(state)
            update_description_if_needed(state)
            
            # Wait before next check
            print(f"Sleeping for {CHECK_INTERVAL} seconds...")
            time.sleep(CHECK_INTERVAL)
            
    except KeyboardInterrupt:
        print("\nStopping dynamic playlist updater...")
        save_state(state)

def main():
    """Entry point"""
    run_dynamic_updater()

if __name__ == "__main__":
    main()