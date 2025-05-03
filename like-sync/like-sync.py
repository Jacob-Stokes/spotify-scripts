import spotipy
from spotipy.oauth2 import SpotifyOAuth
import time
import datetime
import json
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from parent directory .env file
parent_dir = Path(__file__).parent.parent
env_path = parent_dir / '.env'
load_dotenv(dotenv_path=env_path)

# ====== CONFIGURATION ======
SCOPE = os.getenv("SPOTIFY_SCOPE", "user-library-read playlist-modify-public playlist-modify-private")
CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:8888/callback")
STATE_FILE = os.getenv("STATE_FILE", "liked_songs_state.json")
GLOBAL_PLAYLIST_NAME = os.getenv("GLOBAL_PLAYLIST_NAME")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "10"))  # seconds

# ====== SPOTIFY AUTH ======
sp_oauth = SpotifyOAuth(
    scope=SCOPE,
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    redirect_uri=REDIRECT_URI
)

auth_url = sp_oauth.get_authorize_url()
print(f"\nOpen this URL in your browser to authenticate:\n{auth_url}\n")

sp = spotipy.Spotify(auth_manager=sp_oauth)

# ====== STATE MANAGEMENT ======
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

# ====== PLAYLIST HELPERS ======
def build_playlist_index():
    index = {}
    results = sp.current_user_playlists(limit=50)
    while results:
        for playlist in results['items']:
            index[playlist['name'].lower()] = playlist['id']
        if results['next']:
            results = sp.next(results)
        else:
            break
    return index

def create_playlist(name):
    user_id = sp.current_user()['id']
    playlist = sp.user_playlist_create(user_id, name, public=True)
    print(f"Created playlist: {name}")
    return playlist['id']

def ensure_playlist(name, index):
    key = name.lower()
    if key in index:
        return index[key]
    else:
        playlist_id = create_playlist(name)
        index[key] = playlist_id
        return playlist_id

# ====== TRACK HELPERS ======
def get_recent_liked_songs(limit=50):
    results = sp.current_user_saved_tracks(limit=limit)
    tracks = []
    for item in results['items']:
        track = item['track']
        tracks.append({
            'id': track['id'],
            'name': track['name'],
            'artists': ', '.join([artist['name'] for artist in track['artists']])
        })
    return tracks

def add_track_to_playlist(playlist_id, track_id):
    sp.playlist_add_items(playlist_id, [track_id])

def format_month_year():
    now = datetime.datetime.now()
    return now.strftime('%b').upper() + now.strftime('%y')

def format_year():
    now = datetime.datetime.now()
    return f"({now.year})"

# ====== MAIN LOOP ======
def main():
    state = load_state()
    last_processed_id = state.get('last_liked_id')
    playlist_index = build_playlist_index()

    while True:
        try:
            tracks = get_recent_liked_songs(limit=50)
            new_tracks = []

            for track in tracks:
                if track['id'] == last_processed_id:
                    break
                new_tracks.append(track)

            if new_tracks:
                print(f"Found {len(new_tracks)} new liked song(s)")
                for track in reversed(new_tracks):
                    print(f"Adding: {track['name']} by {track['artists']}")

                    month_playlist = format_month_year()
                    year_playlist = format_year()
                    global_playlist = GLOBAL_PLAYLIST_NAME

                    month_id = ensure_playlist(month_playlist, playlist_index)
                    year_id = ensure_playlist(year_playlist, playlist_index)
                    global_id = ensure_playlist(global_playlist, playlist_index)

                    add_track_to_playlist(month_id, track['id'])
                    add_track_to_playlist(year_id, track['id'])
                    add_track_to_playlist(global_id, track['id'])

                    last_processed_id = track['id']
                    state['last_liked_id'] = last_processed_id
                    save_state(state)
            else:
                print("No new liked songs.")

        except Exception as e:
            print(f"[Error] {e}")

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()