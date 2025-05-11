#!/usr/bin/env python3
"""
Last.fm Scrobble Stats to Spotify Playlist Description Sync

This script:
1. Fetches scrobble counts from Last.fm for different time periods
2. Updates a specified Spotify playlist's description with these stats
3. Runs every hour using a simple scheduler
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
SPOTIFY_SCOPE = os.getenv("SPOTIFY_SCOPE", "playlist-modify-public playlist-modify-private user-read-private")

# Sync settings
SPOTIFY_STATS_PLAYLIST_ID = os.getenv("SPOTIFY_STATS_PLAYLIST_ID")
POLL_INTERVAL = int(os.getenv("LASTFM_STATS_POLL_INTERVAL", "3600"))  # Default: 1 hour
STATE_FILE = os.getenv("LASTFM_STATS_STATE_FILE", "lastfm_stats_state.json")

# Fail early if required env vars are missing
required_vars = [
    "LASTFM_API_KEY", "LASTFM_API_SECRET", "LASTFM_USERNAME", 
    "SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_STATS_PLAYLIST_ID"
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
    return {"last_sync": None, "last_stats": {}}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

# ====== LAST.FM API ======
def get_lastfm_scrobble_counts():
    """
    Get scrobble counts from Last.fm for different time periods
    
    Returns:
        dict: Dictionary with scrobble counts for today, week, month, year, and all time
    """
    url = "https://ws.audioscrobbler.com/2.0/"
    
    # Function to get scrobble count for a specific period
    def get_count_for_period(period=None, from_timestamp=None, to_timestamp=None):
        params = {
            'method': 'user.getrecenttracks',
            'user': LASTFM_USERNAME,
            'api_key': LASTFM_API_KEY,
            'format': 'json',
            'limit': 1,  # We just need the total count from metadata
        }
        
        if period:
            # Last.fm specific time periods
            if period in ['overall', '7day', '1month', '3month', '6month', '12month']:
                # For these periods, we need a different API method
                params['method'] = 'user.gettoptracks'
                params['period'] = period
        
        if from_timestamp:
            params['from'] = from_timestamp
        
        if to_timestamp:
            params['to'] = to_timestamp
            
        response = requests.get(url, params=params)
        if response.status_code != 200:
            print(f"Error fetching scrobble count from Last.fm: {response.status_code}")
            print(response.text)
            return 0
        
        data = response.json()
        
        # Different methods return different JSON structures
        if params['method'] == 'user.getrecenttracks':
            # Extract total plays from metadata
            return int(data.get('recenttracks', {}).get('@attr', {}).get('total', 0))
        elif params['method'] == 'user.gettoptracks':
            # For top tracks, we need to sum the playcounts
            tracks = data.get('toptracks', {}).get('track', [])
            return sum(int(track.get('playcount', 0)) for track in tracks)
        
        return 0
    
    # Get current timestamp
    now = int(time.time())
    
    # Calculate timestamps for different periods
    today_start = int(datetime.datetime.combine(datetime.date.today(), datetime.time.min).timestamp())
    week_start = int((datetime.datetime.now() - datetime.timedelta(days=7)).timestamp())
    month_start = int((datetime.datetime.now() - datetime.timedelta(days=30)).timestamp())
    year_start = int((datetime.datetime.now() - datetime.timedelta(days=365)).timestamp())
    
    # Get counts for different periods
    today_count = get_count_for_period(from_timestamp=today_start, to_timestamp=now)
    week_count = get_count_for_period(from_timestamp=week_start, to_timestamp=now)
    month_count = get_count_for_period(from_timestamp=month_start, to_timestamp=now)
    year_count = get_count_for_period(from_timestamp=year_start, to_timestamp=now)
    
    # For all-time count, we use a different approach
    params = {
        'method': 'user.getinfo',
        'user': LASTFM_USERNAME,
        'api_key': LASTFM_API_KEY,
        'format': 'json'
    }
    
    response = requests.get(url, params=params)
    all_time_count = 0
    
    if response.status_code == 200:
        data = response.json()
        all_time_count = int(data.get('user', {}).get('playcount', 0))
    
    return {
        'today': today_count,
        'week': week_count,
        'month': month_count,
        'year': year_count,
        'all_time': all_time_count
    }

# ====== SPOTIFY HELPERS ======
def update_playlist_description(playlist_id, description):
    """Update a Spotify playlist's description"""
    try:
        sp.playlist_change_details(playlist_id, description=description)
        print(f"Updated playlist description successfully")
        return True
    except spotipy.exceptions.SpotifyException as e:
        print(f"Error updating playlist description: {e}")
        return False

def format_description(stats):
    """Format the scrobble stats into a clean description string"""
    # Since Spotify doesn't support line breaks in descriptions,
    # we'll use a separator to make it readable
    separator = " | "
    
    # Format the counts with comma separators for readability
    formatted_stats = {
        'today': f"{stats['today']:,}",
        'week': f"{stats['week']:,}",
        'month': f"{stats['month']:,}",
        'year': f"{stats['year']:,}",
        'all_time': f"{stats['all_time']:,}"
    }
    
    # Build the description
    description = (
        f"Last.fm Stats for {LASTFM_USERNAME}{separator}"
        f"Today: {formatted_stats['today']}{separator}"
        f"This Week: {formatted_stats['week']}{separator}"
        f"This Month: {formatted_stats['month']}{separator}"
        f"This Year: {formatted_stats['year']}{separator}"
        f"All Time: {formatted_stats['all_time']}"
    )
    
    # Add a timestamp for reference
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M UTC")
    description += f"{separator}Updated: {now}"
    
    # Ensure we don't exceed Spotify's description limit (300 chars as of now)
    if len(description) > 300:
        # If too long, use shorter format
        description = (
            f"Last.fm Stats{separator}"
            f"Today: {formatted_stats['today']}{separator}"
            f"Week: {formatted_stats['week']}{separator}"
            f"Month: {formatted_stats['month']}{separator}"
            f"Year: {formatted_stats['year']}{separator}"
            f"All: {formatted_stats['all_time']}"
        )
    
    return description

# ====== MAIN SYNC FUNCTION ======
def sync_lastfm_stats():
    """Sync Last.fm scrobble stats to Spotify playlist description"""
    try:
        # Load previous state
        state = load_state()
        
        # Get current timestamp for logging
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Get scrobble counts from Last.fm
        print(f"[{now}] Fetching scrobble counts from Last.fm...")
        stats = get_lastfm_scrobble_counts()
        
        # Display the stats
        print(f"Last.fm scrobble stats for {LASTFM_USERNAME}:")
        print(f"  Today: {stats['today']:,}")
        print(f"  This Week: {stats['week']:,}")
        print(f"  This Month: {stats['month']:,}")
        print(f"  This Year: {stats['year']:,}")
        print(f"  All Time: {stats['all_time']:,}")
        
        # Check if stats have changed
        last_stats = state.get('last_stats', {})
        stats_changed = stats != last_stats
        
        if not stats_changed:
            print("Scrobble counts haven't changed since last sync")
            return False
        
        # Get playlist info for better logging
        playlist_info = sp.playlist(SPOTIFY_STATS_PLAYLIST_ID, fields='name')
        playlist_name = playlist_info['name']
        
        # Format description and update playlist
        description = format_description(stats)
        print(f"Updating playlist '{playlist_name}' ({SPOTIFY_STATS_PLAYLIST_ID}) description...")
        
        if update_playlist_description(SPOTIFY_STATS_PLAYLIST_ID, description):
            # Update state on success
            state['last_sync'] = datetime.datetime.now().isoformat()
            state['last_stats'] = stats
            save_state(state)
            
            print(f"[{now}] Sync completed successfully")
            return True
        else:
            print(f"[{now}] Failed to update playlist description")
            return False
        
    except requests.exceptions.RequestException as e:
        print(f"Network error when connecting to Last.fm or Spotify: {e}")
        return False
    except spotipy.exceptions.SpotifyException as e:
        print(f"Spotify API error: {e}")
        return False
    except Exception as e:
        print(f"Unexpected error: {e}")
        return False

# ====== MAIN LOOP ======
def main():
    """Main function with polling loop"""
    # Get playlist info for better display
    try:
        playlist_info = sp.playlist(SPOTIFY_STATS_PLAYLIST_ID, fields='name,owner(display_name)')
        playlist_name = playlist_info['name']
        playlist_owner = playlist_info['owner']['display_name']
        
        print("\n" + "="*50)
        print(f"Starting Last.fm Stats to Spotify Description Sync")
        print(f"Last.fm User: {LASTFM_USERNAME}")
        print(f"Target Playlist: '{playlist_name}' (owned by {playlist_owner})")
        print(f"Poll Interval: {POLL_INTERVAL} seconds ({POLL_INTERVAL/60:.1f} minutes)")
        print("="*50 + "\n")
    except Exception as e:
        print("\n" + "="*50)
        print(f"Starting Last.fm Stats to Spotify Description Sync")
        print(f"Last.fm User: {LASTFM_USERNAME}")
        print(f"Target Playlist ID: {SPOTIFY_STATS_PLAYLIST_ID}")
        print(f"Poll Interval: {POLL_INTERVAL} seconds ({POLL_INTERVAL/60:.1f} minutes)")
        print(f"Warning: Could not fetch playlist details: {e}")
        print("="*50 + "\n")
    
    consecutive_errors = 0
    max_consecutive_errors = 5
    
    while True:
        try:
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[{now}] Starting sync...")
            
            success = sync_lastfm_stats()
            
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