#!/usr/bin/env python3
"""
Last.fm Top Tracks Sync

This script:
1. Fetches your top 10 tracks from Last.fm for the last 7 days
2. Clears a specified Spotify playlist
3. Adds the top tracks to the playlist
4. Runs every hour using a simple scheduler
"""

import os
import time
import json
import datetime
import requests
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from parent directory .env file    
parent_dir = Path(__file__).parent.parent
env_path = parent_dir / '.env'
load_dotenv(dotenv_path=env_path)

# ====== CONFIGURATION ======
# Last.fm API settings
LASTFM_API_KEY = os.getenv("LASTFM_API_KEY")
LASTFM_API_SECRET = os.getenv("LASTFM_API_SECRET")
LASTFM_USERNAME = os.getenv("LASTFM_USERNAME")

# Spotify API settings
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:8888/callback")
SPOTIFY_SCOPE = os.getenv("LASTFM_SPOTIFY_SCOPE", "playlist-modify-public playlist-modify-private user-read-private")

# Sync settings
SPOTIFY_PLAYLIST_ID = os.getenv("LASTFM_SPOTIFY_PLAYLIST_ID")
POLL_INTERVAL = int(os.getenv("LASTFM_POLL_INTERVAL", "3600"))  # Default: 1 hour
STATE_FILE = os.getenv("LASTFM_STATE_FILE", "lastfm_sync_state.json")
TOPTRACK_NUMBER = int(os.getenv("LASTFM_TOPTRACK_NUMBER", "10"))  # Default: 10 tracks

    
# Fail early if required env vars are missing
required_vars = [
    "LASTFM_API_KEY", "LASTFM_API_SECRET", "LASTFM_USERNAME", 
    "SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "LASTFM_SPOTIFY_PLAYLIST_ID"
]

# Check using os.getenv() instead of locals()
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

# Get authorization URL for first-time setup if needed
auth_url = sp_oauth.get_authorize_url()
print(f"\nOpen this URL in your browser to authenticate with Spotify if needed:\n{auth_url}\n")

sp = spotipy.Spotify(auth_manager=sp_oauth)

# ====== STATE MANAGEMENT ======
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {"last_sync": None, "last_tracks": []}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

# ====== LAST.FM API ======
def get_lastfm_top_tracks(period='7day', limit=TOPTRACK_NUMBER):
    """
    Get top tracks from Last.fm
    
    Args:
        period (str): Time period ('overall', '7day', '1month', '3month', '6month', '12month')
        limit (int): Number of tracks to fetch
        
    Returns:
        list: List of dicts with artist, track name, and play count
    """
    url = "https://ws.audioscrobbler.com/2.0/"
    params = {
        'method': 'user.gettoptracks',
        'user': LASTFM_USERNAME,
        'api_key': LASTFM_API_KEY,
        'format': 'json',
        'period': period,
        'limit': limit
    }
    
    response = requests.get(url, params=params)
    if response.status_code != 200:
        print(f"Error fetching top tracks from Last.fm: {response.status_code}")
        print(response.text)
        return []
    
    data = response.json()
    tracks = []
    
    for track in data.get('toptracks', {}).get('track', []):
        tracks.append({
            'artist': track['artist']['name'],
            'name': track['name'],
            'playcount': int(track['playcount'])
        })
    
    return tracks

# ====== SPOTIFY HELPERS ======
def search_spotify_track(artist, track_name):
    """
    Search for a track on Spotify
    
    Args:
        artist (str): Artist name
        track_name (str): Track name
        
    Returns:
        str: Spotify track ID, or None if not found
    """
    # Try exact search first
    query = f"track:{track_name} artist:{artist}"
    results = sp.search(q=query, type='track', limit=1)
    
    items = results.get('tracks', {}).get('items', [])
    if items:
        print(f"Found track: {track_name} by {artist}")
        return items[0]['id']
    
    # If exact search fails, try a more relaxed search
    query = f"{track_name} {artist}"
    results = sp.search(q=query, type='track', limit=5)
    
    items = results.get('tracks', {}).get('items', [])
    if items:
        # Look for a good match among the results
        for item in items:
            item_artist = item['artists'][0]['name'].lower()
            item_track = item['name'].lower()
            
            # Check if both artist and track name are similar
            if (artist.lower() in item_artist or item_artist in artist.lower()) and \
               (track_name.lower() in item_track or item_track in track_name.lower()):
                print(f"Found similar track: {item['name']} by {item['artists'][0]['name']}")
                return item['id']
    
    print(f"Track not found on Spotify: {track_name} by {artist}")
    return None

def clear_playlist(playlist_id):
    """Clear all tracks from a Spotify playlist"""
    # Get all tracks in the playlist (handle pagination)
    results = sp.playlist_items(playlist_id, fields='items(track(id)),next')
    track_ids = []
    
    # Collect all track IDs, handling pagination
    while results:
        track_ids.extend([item['track']['id'] for item in results['items'] if item['track']])
        
        if results['next']:
            results = sp.next(results)
        else:
            break
    
    if not track_ids:
        print("Playlist is already empty")
        return
    
    # Spotify API can only remove 100 tracks at a time
    for i in range(0, len(track_ids), 100):
        batch = track_ids[i:i+100]
        sp.playlist_remove_all_occurrences_of_items(playlist_id, batch)
    
    print(f"Cleared {len(track_ids)} tracks from playlist")

def add_tracks_to_playlist(playlist_id, track_ids):
    """Add tracks to a Spotify playlist"""
    if not track_ids:
        print("No tracks to add")
        return
    
    # Spotify API can only add 100 tracks at a time
    for i in range(0, len(track_ids), 100):
        batch = track_ids[i:i+100]
        sp.playlist_add_items(playlist_id, batch)
    
    print(f"Added {len(track_ids)} tracks to playlist")

# ====== MAIN SYNC FUNCTION ======
def sync_lastfm_top_tracks():
    """Sync Last.fm top tracks to Spotify playlist"""
    try:
        # Load previous state
        state = load_state()
        
        # Get current timestamp for logging
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Get top tracks from Last.fm
        print(f"[{now}] Fetching top tracks from Last.fm...")
        top_tracks = get_lastfm_top_tracks(period='7day', limit=10)
        
        if not top_tracks:
            print("No tracks found in Last.fm top tracks")
            return False
        
        # Display the top tracks
        print(f"Found {len(top_tracks)} top tracks on Last.fm:")
        for i, track in enumerate(top_tracks, 1):
            print(f"  {i}. {track['name']} by {track['artist']} ({track['playcount']} plays)")
        
        # Search for tracks on Spotify
        print("Searching for tracks on Spotify...")
        spotify_track_ids = []
        spotify_tracks_info = []  # Store track info for logging
        
        for track in top_tracks:
            track_id = search_spotify_track(track['artist'], track['name'])
            if track_id:
                spotify_track_ids.append(track_id)
                # Get track info for display
                track_info = sp.track(track_id)
                spotify_tracks_info.append({
                    'id': track_id,
                    'name': track_info['name'],
                    'artist': track_info['artists'][0]['name']
                })
        
        if not spotify_track_ids:
            print("No matching tracks found on Spotify")
            return False
        
        print(f"Found {len(spotify_track_ids)} matching tracks on Spotify")
        
        # Check if tracks have changed
        last_tracks = state.get('last_tracks', [])
        tracks_changed = (set(spotify_track_ids) != set(last_tracks))
        
        if not tracks_changed:
            print("Top tracks haven't changed since last sync")
            return False
        
        # Get playlist info for better logging
        playlist_info = sp.playlist(SPOTIFY_PLAYLIST_ID, fields='name')
        playlist_name = playlist_info['name']
        
        # Clear the playlist and add new tracks
        print(f"Updating playlist '{playlist_name}' ({SPOTIFY_PLAYLIST_ID})...")
        clear_playlist(SPOTIFY_PLAYLIST_ID)
        add_tracks_to_playlist(SPOTIFY_PLAYLIST_ID, spotify_track_ids)
        
        # Display the tracks being added
        print(f"Added {len(spotify_tracks_info)} tracks to '{playlist_name}':")
        for i, track in enumerate(spotify_tracks_info, 1):
            print(f"  {i}. {track['name']} by {track['artist']}")
        
        # Update state
        state['last_sync'] = datetime.datetime.now().isoformat()
        state['last_tracks'] = spotify_track_ids
        save_state(state)
        
        print(f"[{now}] Sync completed successfully")
        return True
        
    except requests.exceptions.RequestException as e:
        print(f"Network error when connecting to Last.fm or Spotify: {e}")
        return False
    except spotipy.exceptions.SpotifyException as e:
        print(f"Spotify API error: {e}")
        return False

# ====== MAIN LOOP ======
def main():
    """Main function with polling loop"""
    # Get playlist info for better display
    try:
        playlist_info = sp.playlist(SPOTIFY_PLAYLIST_ID, fields='name,owner(display_name)')
        playlist_name = playlist_info['name']
        playlist_owner = playlist_info['owner']['display_name']
        
        print("\n" + "="*50)
        print(f"Starting Last.fm to Spotify Top Tracks Sync")
        print(f"Last.fm User: {LASTFM_USERNAME}")
        print(f"Target Playlist: '{playlist_name}' (owned by {playlist_owner})")
        print(f"Poll Interval: {POLL_INTERVAL} seconds ({POLL_INTERVAL/60:.1f} minutes)")
        print("="*50 + "\n")
    except Exception as e:
        print("\n" + "="*50)
        print(f"Starting Last.fm to Spotify Top Tracks Sync")
        print(f"Last.fm User: {LASTFM_USERNAME}")
        print(f"Target Playlist ID: {SPOTIFY_PLAYLIST_ID}")
        print(f"Poll Interval: {POLL_INTERVAL} seconds ({POLL_INTERVAL/60:.1f} minutes)")
        print(f"Warning: Could not fetch playlist details: {e}")
        print("="*50 + "\n")
    
    consecutive_errors = 0
    max_consecutive_errors = 5
    
    while True:
        try:
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[{now}] Starting sync...")
            
            success = sync_lastfm_top_tracks()
            
            # Reset error counter on success
            if success:
                consecutive_errors = 0
                
        except KeyboardInterrupt:
            print("\nScript terminated by user. Exiting...")
            break
        except Exception as e:
            consecutive_errors += 1
            print(f"[Error] {e}")
            
            # If we've had too many consecutive errors, increase the wait time
            if consecutive_errors >= max_consecutive_errors:
                backoff_time = min(POLL_INTERVAL * 5, 3600)  # Max 1 hour backoff
                print(f"Too many consecutive errors ({consecutive_errors}). Backing off for {backoff_time} seconds...")
                time.sleep(backoff_time)
                continue
        
        # Format remaining time nicely
        if POLL_INTERVAL >= 3600:
            time_str = f"{POLL_INTERVAL / 3600:.1f} hours"
        elif POLL_INTERVAL >= 60:
            time_str = f"{POLL_INTERVAL / 60:.1f} minutes"
        else:
            time_str = f"{POLL_INTERVAL} seconds"
            
        print(f"Waiting {time_str} before next sync...")
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()