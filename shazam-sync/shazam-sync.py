import spotipy
from spotipy.oauth2 import SpotifyOAuth
import time
import json
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from parent directory .env file
parent_dir = Path(__file__).parent.parent
env_path = parent_dir / '.env'
load_dotenv(dotenv_path=env_path)

# ====== CONFIG ======
SCOPE = os.getenv("SPOTIFY_SCOPE", "playlist-read-private playlist-modify-public playlist-modify-private")
CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:8888/callback")
SOURCE_PLAYLIST_ID = os.getenv("SOURCE_PLAYLIST_ID")
DEST_PLAYLIST_ID = os.getenv("DEST_PLAYLIST_ID")
STATE_FILE = os.getenv("SHAZAM_STATE_FILE", "shazam_sync_state.json")
POLL_INTERVAL = int(os.getenv("SHAZAM_POLL_INTERVAL", "300"))

# Fail early if required env vars are missing
if not SOURCE_PLAYLIST_ID or not DEST_PLAYLIST_ID:
    raise ValueError("SOURCE_PLAYLIST_ID and DEST_PLAYLIST_ID must be set in the .env file")

# ====== AUTH ======
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    scope=SCOPE,
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    redirect_uri=REDIRECT_URI
))

# ====== STATE MANAGEMENT ======
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

# ====== GET TRACKS FROM PLAYLIST ======
def get_tracks_from_playlist(playlist_id):
    results = sp.playlist_items(playlist_id, limit=100)
    return [
        {
            'id': item['track']['id'],
            'name': item['track']['name'],
            'artist': ', '.join([a['name'] for a in item['track']['artists']])
        }
        for item in results['items']
        if item['track'] and item['track']['id']
    ]

# ====== SYNC ======
def sync_shazam_to_field():
    state = load_state()
    last_synced_id = state.get('last_synced_shazam_id')
    tracks = get_tracks_from_playlist(SOURCE_PLAYLIST_ID)

    if not tracks:
        print("No tracks found in source playlist.")
        return

    if not last_synced_id:
        state['last_synced_shazam_id'] = tracks[0]['id']
        save_state(state)
        print(f"Baseline set: {tracks[0]['name']} by {tracks[0]['artist']}")
        return

    new_tracks = []
    for track in tracks:
        if track['id'] == last_synced_id:
            break
        new_tracks.append(track)

    if new_tracks:
        for track in reversed(new_tracks):
            print(f"Adding: {track['name']} by {track['artist']}")
            sp.playlist_add_items(DEST_PLAYLIST_ID, [track['id']])
            state['last_synced_shazam_id'] = track['id']
            save_state(state)
    else:
        print("No new tracks to sync.")

# ====== LOOP ======
def main():
    while True:
        try:
            sync_shazam_to_field()
        except Exception as e:
            print(f"[Error] {e}")
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()