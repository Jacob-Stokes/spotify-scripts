import spotipy
from spotipy.oauth2 import SpotifyOAuth
import argparse
import os
import json
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from parent directory .env file
parent_dir = Path(__file__).parent.parent
env_path = parent_dir / '.env'
load_dotenv(dotenv_path=env_path)

# ====== CONFIG ======
SCOPE = os.getenv("FESTIVAL_SCOPE", "playlist-modify-public playlist-modify-private user-read-private")
CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:8888/callback")

# ====== AUTH ======
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    redirect_uri=REDIRECT_URI,
    scope=SCOPE
))

# ====== HELPERS ======
def get_artist_id(name):
    results = sp.search(q=f"artist:{name}", type='artist', limit=1)
    items = results['artists']['items']
    return items[0]['id'] if items else None

def get_top_tracks(artist_id, limit):
    results = sp.artist_top_tracks(artist_id, country='US')
    return [track['id'] for track in results['tracks'][:limit]]

def load_seen(seen_file):
    if os.path.exists(seen_file):
        with open(seen_file, 'r') as f:
            return set(json.load(f))
    return set()

def save_seen(seen_set, seen_file):
    with open(seen_file, 'w') as f:
        json.dump(sorted(list(seen_set)), f)

def write_log(lines, log_file):
    with open(log_file, 'w') as f:
        for line in lines:
            f.write(line + "\n")

def clear_playlist(playlist_id):
    results = sp.playlist_items(playlist_id, limit=100)
    uris = [item['track']['uri'] for item in results['items'] if item['track']]
    if uris:
        sp.playlist_replace_items(playlist_id, [])
        print(f"🧨 Playlist cleared.")

# ====== MAIN ======
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("lineup_file", help="Text file with one artist per line")
    parser.add_argument("playlist_id", help="Target Spotify playlist ID")
    parser.add_argument("--top", type=int, default=1, help="Number of top tracks to add per artist")
    parser.add_argument("--overwrite", action="store_true", help="Clear playlist before adding new tracks")
    parser.add_argument("--reset", action="store_true", help="Clear the seen-artists file and start fresh")
    args = parser.parse_args()

    if not os.path.exists(args.lineup_file):
        print(f"❌ File not found: {args.lineup_file}")
        return

    # Paths and filenames
    base_path = os.path.dirname(os.path.abspath(args.lineup_file))
    base_name = os.path.splitext(os.path.basename(args.lineup_file))[0]
    seen_file = os.path.join(base_path, f"{base_name}_seen-artists.json")
    log_file = os.path.join(base_path, f"{base_name}_log.txt")

    # Optional reset
    if args.reset and os.path.exists(seen_file):
        os.remove(seen_file)
        print(f"🔁 Reset: {seen_file} cleared.")

    # Load artist list
    with open(args.lineup_file, 'r') as f:
        all_artists = [line.strip() for line in f if line.strip()]

    seen_artists = load_seen(seen_file)
    new_artists = [a for a in all_artists if a not in seen_artists]

    if not new_artists:
        print("🟡 No new artists to process.")
        return

    if args.overwrite:
        clear_playlist(args.playlist_id)

    log = []
    all_tracks = []

    for artist in new_artists:
        print(f"🎤 {artist}")
        artist_id = get_artist_id(artist)
        if artist_id:
            tracks = get_top_tracks(artist_id, args.top)
            if tracks:
                all_tracks.extend(tracks)
                log.append(f"[ADDED] {artist}: {len(tracks)} tracks")
                print(f"  ➕ {len(tracks)} added")
                seen_artists.add(artist)
            else:
                log.append(f"[NO TRACKS] {artist}")
                print(f"  ⚠️ No top tracks found")
        else:
            log.append(f"[NOT FOUND] {artist}")
            print(f"  ❌ Artist not found")

    for i in range(0, len(all_tracks), 100):
        sp.playlist_add_items(args.playlist_id, all_tracks[i:i+100])

    save_seen(seen_artists, seen_file)
    write_log(log, log_file)

    print(f"\n✅ Done. {len(all_tracks)} tracks added.")
    print(f"📝 Log written to: {log_file}")
    print(f"📁 Seen list updated: {seen_file}")

if __name__ == "__main__":
    main()