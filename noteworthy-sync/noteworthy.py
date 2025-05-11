#!/usr/bin/env python3
"""
Noteworthy Playlist Manager

This script:
1. Monitors a "Noteworthy" playlist
2. Adds all songs from Noteworthy to a "Noteworthy Archive" playlist
3. Removes songs from the Noteworthy playlist after they've been there for 7 days
4. Maintains a record of when songs were added to properly track the 7-day period
"""

import os
import time as sleep_module
import datetime
import ssl
import certifi
import requests
import spotipy
import argparse
import json
from spotipy.oauth2 import SpotifyOAuth
from pathlib import Path
from dotenv import load_dotenv
import logging

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('noteworthy_manager.log')
    ]
)
logger = logging.getLogger(__name__)

# SSL certificate fix for macOS
ssl_context = ssl.create_default_context(cafile=certifi.where())
ssl._create_default_https_context = lambda: ssl_context

# Load environment variables from parent directory .env file    
parent_dir = Path(__file__).parent.parent
env_path = parent_dir / '.env'
load_dotenv(dotenv_path=env_path)

# Parse command line arguments
parser = argparse.ArgumentParser(description="Noteworthy Playlist Manager")
parser.add_argument("--check-interval", type=int, default=3600, 
                    help="Check interval in seconds (default: 3600, 1 hour)")
parser.add_argument("--retention-days", type=int, default=7,
                    help="Number of days to keep songs in Noteworthy playlist (default: 7)")
parser.add_argument("--dry-run", action="store_true",
                    help="Dry run mode - don't actually modify playlists")
parser.add_argument("--force-cleanup", action="store_true",
                    help="Force cleanup of state file - remove tracks no longer in Noteworthy")
args = parser.parse_args()

# ====== CONFIGURATION ======
# Spotify API settings
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:8888/callback")
SPOTIFY_SCOPE = "playlist-read-private playlist-read-collaborative playlist-modify-private playlist-modify-public"

# Playlist IDs
NOTEWORTHY_PLAYLIST_ID = os.getenv("NOTEWORTHY_PLAYLIST_ID")
NOTEWORTHY_ARCHIVE_PLAYLIST_ID = os.getenv("NOTEWORTHY_ARCHIVE_PLAYLIST_ID")

# State file to track song add dates
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'noteworthy_state.json')

# Check interval and retention period
CHECK_INTERVAL = args.check_interval  # Seconds between checks
RETENTION_DAYS = args.retention_days  # Days to keep songs in Noteworthy
DRY_RUN = args.dry_run  # Dry run mode
FORCE_CLEANUP = args.force_cleanup  # Force cleanup of state file

# Fail early if required env vars are missing
required_vars = [
    "SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", 
    "NOTEWORTHY_PLAYLIST_ID", "NOTEWORTHY_ARCHIVE_PLAYLIST_ID"
]

# Check using os.getenv() instead of locals()
missing_vars = [var for var in required_vars if not os.getenv(var)]
if missing_vars:
    raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

# Initialize Spotify client (will be initialized when needed)
sp = None

# ====== SPOTIFY AUTH ======
def initialize_spotify():
    """Initialize Spotify client if not already initialized"""
    global sp
    if sp is not None:
        return
        
    sp_oauth = SpotifyOAuth(
        scope=SPOTIFY_SCOPE,
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI
    )

    auth_url = sp_oauth.get_authorize_url()
    logger.info(f"Open this URL in your browser to authenticate with Spotify if needed:\n{auth_url}")

    sp = spotipy.Spotify(auth_manager=sp_oauth)

# ====== PLAYLIST MANAGEMENT ======
def get_playlist_tracks(playlist_id):
    """
    Get all tracks from a playlist
    
    Args:
        playlist_id (str): Spotify playlist ID
        
    Returns:
        list: List of track objects with IDs and add dates
    """
    initialize_spotify()
    
    # Get playlist info
    try:
        playlist = sp.playlist(playlist_id)
        playlist_name = playlist["name"]
        total_tracks = playlist["tracks"]["total"]
        
        logger.info(f"Getting tracks from playlist '{playlist_name}' (ID: {playlist_id})")
        logger.info(f"Total tracks: {total_tracks}")
        
        tracks = []
        offset = 0
        limit = 100  # Max number of tracks per request
        
        # Use pagination to get all tracks manually instead of using 'next'
        while offset < total_tracks:
            results = sp.playlist_items(
                playlist_id,
                offset=offset,
                limit=limit,
                fields="items(added_at,track(id,name,artists,album(name)))",
                additional_types=["track"]
            )
            
            batch = results.get('items', [])
            tracks.extend(batch)
            offset += limit
            
            # Safety check
            if len(batch) == 0:
                break
                
        logger.info(f"Retrieved {len(tracks)} tracks from playlist '{playlist_name}'")
        return tracks
    except Exception as e:
        logger.error(f"Error getting tracks from playlist {playlist_id}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return []

def get_formatted_track_info(track_item):
    """
    Get formatted track information from a track item
    
    Args:
        track_item (dict): Track item from playlist items
        
    Returns:
        dict: Formatted track information
    """
    track = track_item["track"]
    
    # Track might be None if it's been removed from Spotify
    if track is None:
        return {
            "id": "unknown",
            "name": "Unknown Track (Removed)",
            "artists": "Unknown Artist",
            "album": "Unknown Album",
            "added_at": track_item.get("added_at", "")
        }
        
    added_at = track_item["added_at"]
    
    # Format artist names
    artist_names = [artist["name"] for artist in track["artists"]]
    artists = ", ".join(artist_names)
    
    return {
        "id": track["id"],
        "name": track["name"],
        "artists": artists,
        "album": track["album"]["name"],
        "added_at": added_at
    }

def load_state():
    """
    Load noteworthy state from file
    
    Returns:
        dict: Noteworthy state
    """
    if not os.path.exists(STATE_FILE):
        return {"tracks": {}}
        
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
            
        logger.info(f"Loaded state with {len(state.get('tracks', {}))} tracked tracks")
        return state
    except Exception as e:
        logger.error(f"Error loading state file: {e}")
        return {"tracks": {}}

def save_state(state):
    """
    Save noteworthy state to file
    
    Args:
        state (dict): Noteworthy state
    """
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
            
        logger.info(f"Saved state with {len(state.get('tracks', {}))} tracked tracks")
    except Exception as e:
        logger.error(f"Error saving state file: {e}")

def update_noteworthy_playlists():
    """Update noteworthy and archive playlists"""
    initialize_spotify()
    
    # Get playlists info for better logging
    try:
        noteworthy_info = sp.playlist(NOTEWORTHY_PLAYLIST_ID, fields="name")
        archive_info = sp.playlist(NOTEWORTHY_ARCHIVE_PLAYLIST_ID, fields="name")
        noteworthy_name = noteworthy_info["name"]
        archive_name = archive_info["name"]
    except Exception as e:
        logger.error(f"Error getting playlist info: {e}")
        noteworthy_name = "Unknown"
        archive_name = "Unknown"
    
    logger.info("=" * 50)
    logger.info(f"Updating Noteworthy playlists")
    logger.info(f"Noteworthy: '{noteworthy_name}' (ID: {NOTEWORTHY_PLAYLIST_ID})")
    logger.info(f"Archive: '{archive_name}' (ID: {NOTEWORTHY_ARCHIVE_PLAYLIST_ID})")
    logger.info(f"Retention period: {RETENTION_DAYS} days")
    if DRY_RUN:
        logger.info("DRY RUN MODE - No changes will be made to playlists")
    if FORCE_CLEANUP:
        logger.info("FORCE CLEANUP MODE - Will remove tracks from state that are no longer in Noteworthy")
    logger.info("=" * 50)
    
    # Load state to track when songs were added
    state = load_state()
    if "tracks" not in state:
        state["tracks"] = {}
        
    # Get current noteworthy tracks
    noteworthy_tracks = get_playlist_tracks(NOTEWORTHY_PLAYLIST_ID)
    noteworthy_track_ids = []
    for track in noteworthy_tracks:
        if track.get("track") and track["track"].get("id"):
            noteworthy_track_ids.append(track["track"]["id"])
    
    # Get current archive tracks
    archive_tracks = get_playlist_tracks(NOTEWORTHY_ARCHIVE_PLAYLIST_ID)
    archive_track_ids = []
    for track in archive_tracks:
        if track.get("track") and track["track"].get("id"):
            archive_track_ids.append(track["track"]["id"])
    
    # Clean up state for tracks that are no longer in Noteworthy
    tracks_to_remove_from_state = []
    for track_id in list(state["tracks"].keys()):
        if track_id not in noteworthy_track_ids:
            tracks_to_remove_from_state.append(track_id)
            
    # Remove tracks from state that are no longer in Noteworthy
    if FORCE_CLEANUP or len(tracks_to_remove_from_state) > 0:
        for track_id in tracks_to_remove_from_state:
            track_info = state["tracks"][track_id].get("track_info", {"name": "Unknown", "artists": "Unknown"})
            logger.info(f"Removing track from state (no longer in Noteworthy): '{track_info['name']}' by {track_info['artists']}")
            del state["tracks"][track_id]
    
    # Check for new tracks in noteworthy that need to be added to archive
    new_tracks_for_archive = []
    for track in noteworthy_tracks:
        # Skip tracks that don't have valid data
        if not track.get("track") or not track["track"].get("id"):
            continue
            
        track_id = track["track"]["id"]
        
        # Try to get track info, handling potential errors
        try:
            track_info = get_formatted_track_info(track)
        except Exception as e:
            logger.error(f"Error getting track info for {track_id}: {e}")
            continue
        
        # Check if track is already in our state
        if track_id not in state["tracks"]:
            # New track, add it to state
            added_at_iso = track["added_at"]
            
            # Parse the ISO string to a datetime object
            added_at = datetime.datetime.fromisoformat(added_at_iso.replace("Z", "+00:00"))
            
            # Store as ISO string in state
            state["tracks"][track_id] = {
                "added_at": added_at.isoformat(),
                "track_info": track_info
            }
            
            logger.info(f"New track found in Noteworthy: '{track_info['name']}' by {track_info['artists']}")
            
            # If track is not in archive, add it
            if track_id not in archive_track_ids:
                new_tracks_for_archive.append(track_id)
                logger.info(f"Track will be added to Archive: '{track_info['name']}'")
    
    # Check for tracks that need to be removed from noteworthy (older than retention period)
    tracks_to_remove = []
    now = datetime.datetime.now(datetime.timezone.utc)
    
    for track_id, track_data in list(state["tracks"].items()):
        # Skip if track doesn't exist in state or is no longer in Noteworthy
        if not track_data or "added_at" not in track_data or track_id not in noteworthy_track_ids:
            continue
            
        # Parse the stored datetime
        try:
            added_at = datetime.datetime.fromisoformat(track_data["added_at"])
        except (ValueError, TypeError):
            logger.error(f"Invalid date format in state for track {track_id}")
            continue
        
        # Calculate days since added
        days_since_added = (now - added_at).days
        
        # If the track is older than retention period, remove it
        if days_since_added >= RETENTION_DAYS:
            tracks_to_remove.append(track_id)
            track_info = track_data.get("track_info", {"name": "Unknown", "artists": "Unknown"})
            logger.info(f"Track has been in Noteworthy for {days_since_added} days and will be removed: "
                      f"'{track_info['name']}' by {track_info['artists']}")
    
    # Add new tracks to archive
    if new_tracks_for_archive and not DRY_RUN:
        try:
            # Add tracks in batches of 100 (Spotify API limit)
            for i in range(0, len(new_tracks_for_archive), 100):
                batch = new_tracks_for_archive[i:i+100]
                sp.playlist_add_items(NOTEWORTHY_ARCHIVE_PLAYLIST_ID, batch)
            logger.info(f"Added {len(new_tracks_for_archive)} new tracks to Archive playlist")
        except Exception as e:
            logger.error(f"Error adding tracks to Archive playlist: {e}")
            import traceback
            logger.error(traceback.format_exc())
    elif new_tracks_for_archive and DRY_RUN:
        logger.info(f"DRY RUN: Would add {len(new_tracks_for_archive)} tracks to Archive playlist")
    
    # Remove old tracks from noteworthy
    if tracks_to_remove and not DRY_RUN:
        try:
            # Remove tracks in batches of 100 (Spotify API limit)
            for i in range(0, len(tracks_to_remove), 100):
                batch = tracks_to_remove[i:i+100]
                sp.playlist_remove_all_occurrences_of_items(NOTEWORTHY_PLAYLIST_ID, batch)
            logger.info(f"Removed {len(tracks_to_remove)} tracks from Noteworthy playlist (retention period exceeded)")
        except Exception as e:
            logger.error(f"Error removing tracks from Noteworthy playlist: {e}")
            import traceback
            logger.error(traceback.format_exc())
    elif tracks_to_remove and DRY_RUN:
        logger.info(f"DRY RUN: Would remove {len(tracks_to_remove)} tracks from Noteworthy playlist")
    
    # Save updated state
    save_state(state)
    
    # Summary
    logger.info("-" * 50)
    logger.info("Summary:")
    logger.info(f"  Noteworthy playlist: {len(noteworthy_tracks)} tracks")
    logger.info(f"  Archive playlist: {len(archive_tracks)} tracks")
    logger.info(f"  New tracks added to Archive: {len(new_tracks_for_archive)}")
    logger.info(f"  Old tracks removed from Noteworthy: {len(tracks_to_remove)}")
    logger.info(f"  Tracks removed from state: {len(tracks_to_remove_from_state)}")
    logger.info("-" * 50)
    
    return len(new_tracks_for_archive), len(tracks_to_remove)

# ====== MAIN FUNCTION ======
def main():
    """Main function"""
    logger.info("\n" + "="*50)
    logger.info("Starting Noteworthy Playlist Manager")
    logger.info(f"Check interval: {CHECK_INTERVAL} seconds")
    logger.info(f"Retention period: {RETENTION_DAYS} days")
    if DRY_RUN:
        logger.info("DRY RUN MODE - No changes will be made to playlists")
    if FORCE_CLEANUP:
        logger.info("FORCE CLEANUP MODE - Will remove tracks from state that are no longer in Noteworthy")
    logger.info("="*50 + "\n")
    
    # Initialize Spotify
    initialize_spotify()
    
    # First run immediately
    try:
        update_noteworthy_playlists()
    except Exception as e:
        logger.error(f"Error in first run: {e}")
        import traceback
        logger.error(traceback.format_exc())
    
    # Then run periodically
    while True:
        try:
            logger.info(f"Waiting {CHECK_INTERVAL} seconds until next check...")
            sleep_module.sleep(CHECK_INTERVAL)
            logger.info("Checking playlists again...")
            update_noteworthy_playlists()
        except KeyboardInterrupt:
            logger.info("Script terminated by user. Exiting...")
            break
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            import traceback
            logger.error(traceback.format_exc())
            # Sleep a bit to avoid hammering the API in case of repeated errors
            sleep_module.sleep(60)

if __name__ == "__main__":
    main()