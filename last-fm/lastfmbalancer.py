#!/usr/bin/env python3
# Spotify and Last.fm Track Analyzer
# This script compares your Spotify liked songs with your Last.fm scrobbles

import os
import time
import csv
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import pylast
from collections import defaultdict
from tqdm import tqdm

# --- Configuration ---
# Spotify API credentials
SPOTIFY_CLIENT_ID = '1ac3defbf6f1439fb933f7709a94f615'
SPOTIFY_CLIENT_SECRET = 'f2b277d824d34610bae8107e04662f76'
SPOTIFY_REDIRECT_URI = 'http://127.0.0.1:8888/callback'
SPOTIFY_SCOPE = 'user-library-read'

# Last.fm API credentials
LASTFM_API_KEY = '6d6f436206080ea93753b8c701fa67dd'
LASTFM_API_SECRET = 'bcb71e7eb0c3a1139eed08f84fa04010'
LASTFM_USERNAME = 'jthstokes'
LASTFM_PASSWORD = None  # Only needed for specific operations

# Output file paths
OUTPUT_DIR = 'output'
SCROBBLED_NOT_LIKED_FILE = os.path.join(OUTPUT_DIR, 'scrobbled_not_liked.csv')
LIKED_SCROBBLED_ONCE_FILE = os.path.join(OUTPUT_DIR, 'liked_scrobbled_once.csv')
LIKED_NEVER_SCROBBLED_FILE = os.path.join(OUTPUT_DIR, 'liked_never_scrobbled.csv')

def ensure_output_directory():
    """Create output directory if it doesn't exist."""
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

def setup_spotify():
    """Set up and authenticate with Spotify API."""
    print("Authenticating with Spotify...")
    return spotipy.Spotify(auth_manager=SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope=SPOTIFY_SCOPE
    ))

def setup_lastfm():
    """Set up and authenticate with Last.fm API."""
    print("Authenticating with Last.fm...")
    
    # Create network object without authentication first
    network = pylast.LastFMNetwork(
        api_key=LASTFM_API_KEY,
        api_secret=LASTFM_API_SECRET
    )
    
    # Test the connection with a simple call
    try:
        # Try to get user info as a test
        user = network.get_user(LASTFM_USERNAME)
        user.get_name()
        print(f"Successfully authenticated with Last.fm for user: {LASTFM_USERNAME}")
    except pylast.WSError as e:
        print(f"Error authenticating with Last.fm: {e}")
        raise Exception("Failed to authenticate with Last.fm. Please check your API credentials.")
    
    return network

def get_all_spotify_liked_songs(sp):
    """Retrieve all tracks saved in the user's 'Liked Songs' on Spotify."""
    print("Fetching all your Spotify liked songs...")
    results = sp.current_user_saved_tracks(limit=50)
    liked_songs = []
    
    with tqdm(total=None) as pbar:
        while results:
            for item in results['items']:
                track = item['track']
                artist = track['artists'][0]['name']  # Primary artist
                title = track['name']
                liked_songs.append({
                    'artist': artist,
                    'title': title,
                    'id': track['id'],
                    'added_at': item['added_at']
                })
                pbar.update(1)
            
            if results['next']:
                results = sp.next(results)
            else:
                break
    
    print(f"Retrieved {len(liked_songs)} liked songs from Spotify.")
    return liked_songs

def get_all_lastfm_scrobbles(network):
    """Retrieve all user's scrobbles from Last.fm."""
    print("Fetching your Last.fm scrobbles (this may take a while)...")
    user = network.get_user(LASTFM_USERNAME)
    
    # Get total number of scrobbles for progress bar
    try:
        total_scrobbles = user.get_playcount()
        print(f"Total scrobbles to fetch: {total_scrobbles}")
    except Exception as e:
        print(f"Could not get playcount, will fetch anyway: {e}")
        total_scrobbles = None  # will show indefinite progress bar
    
    # Get all scrobbles
    scrobbles = []
    
    # Try simpler approach first with the library's built-in method
    try:
        # Use smaller batches and standard pagination
        batch_size = 50  # Start with a safe small value
        page = 1
        
        with tqdm(total=total_scrobbles) as pbar:
            while True:
                try:
                    # Use the library's method but with smaller batches
                    recent_tracks = user.get_recent_tracks(limit=batch_size)
                    
                    # If we didn't get any tracks, we're done
                    if not recent_tracks:
                        break
                    
                    # Process tracks
                    for track in recent_tracks:
                        try:
                            artist = track.track.artist.name
                            title = track.track.title
                            timestamp = track.timestamp
                            
                            scrobbles.append({
                                'artist': artist,
                                'title': title,
                                'timestamp': timestamp
                            })
                            pbar.update(1)
                        except AttributeError:
                            # Skip tracks that might be missing data
                            continue
                    
                    # If we got fewer tracks than requested, we're at the end
                    if len(recent_tracks) < batch_size:
                        break
                    
                    # Last.fm API has rate limits, so be nice
                    time.sleep(0.5)
                    
                    # Since get_recent_tracks doesn't natively support page parameter,
                    # we'll use the from_date parameter to paginate
                    if recent_tracks:
                        # Get the oldest timestamp
                        oldest_timestamp = None
                        for track in recent_tracks:
                            if oldest_timestamp is None or track.timestamp < oldest_timestamp:
                                oldest_timestamp = track.timestamp
                        
                        if oldest_timestamp:
                            # Next time get tracks older than the oldest one we just got
                            # We need to use keyword arguments as a dictionary
                            kwargs = {'limit': batch_size, 'time_to': oldest_timestamp - 1}
                            recent_tracks = user.get_recent_tracks(**kwargs)
                        else:
                            break
                    else:
                        break
                    
                except pylast.WSError as e:
                    print(f"Error fetching page: {e}")
                    if "limit param" in str(e).lower():
                        # Try with an even smaller batch size
                        batch_size = max(10, batch_size // 2)
                        print(f"Reducing batch size to {batch_size} and retrying...")
                        time.sleep(1)
                        continue
                    elif "rate limit" in str(e).lower():
                        wait_time = 60
                        print(f"Rate limited. Waiting {wait_time} seconds...")
                        time.sleep(wait_time)
                        continue
                    else:
                        print(f"Unexpected error: {e}")
                        if len(scrobbles) > 0:
                            print("Continuing with tracks fetched so far...")
                            break
                        else:
                            raise
    
    except Exception as e:
        print(f"Failed with high-level API method: {e}")
        print("Trying alternative method with direct API access...")
        
        # If high-level method failed, try direct API access
        # This is a fallback approach
        try:
            from xml.dom import minidom
            
            page = 1
            limit = 50  # Smaller limit to avoid issues
            
            with tqdm(total=total_scrobbles) as pbar:
                while True:
                    try:
                        # Direct API request
                        params = {
                            'method': 'user.getRecentTracks',
                            'user': LASTFM_USERNAME,
                            'api_key': LASTFM_API_KEY,
                            'limit': limit,
                            'page': page,
                        }
                        
                        # Manual request to Last.fm API
                        import urllib.request
                        import urllib.parse
                        
                        # Build URL
                        url = 'http://ws.audioscrobbler.com/2.0/?' + urllib.parse.urlencode(params)
                        
                        # Make request
                        response = urllib.request.urlopen(url)
                        content = response.read()
                        
                        # Parse XML
                        doc = minidom.parseString(content)
                        
                        # Get tracks
                        track_elements = doc.getElementsByTagName('track')
                        
                        if not track_elements:
                            break
                        
                        # Process tracks
                        for track_element in track_elements:
                            # Skip now playing
                            if track_element.getAttribute('nowplaying') == 'true':
                                continue
                            
                            # Extract data
                            artist_element = track_element.getElementsByTagName('artist')[0]
                            artist = artist_element.firstChild.data if artist_element.firstChild else "Unknown Artist"
                            
                            name_element = track_element.getElementsByTagName('name')[0]
                            title = name_element.firstChild.data if name_element.firstChild else "Unknown Track"
                            
                            date_element = track_element.getElementsByTagName('date')[0]
                            timestamp = int(date_element.getAttribute('uts')) if date_element else 0
                            
                            scrobbles.append({
                                'artist': artist,
                                'title': title,
                                'timestamp': timestamp
                            })
                            pbar.update(1)
                        
                        # If we got fewer than requested, we're done
                        if len(track_elements) < limit:
                            break
                        
                        # Next page
                        page += 1
                        
                        # Rate limit
                        time.sleep(0.5)
                        
                    except Exception as e:
                        print(f"Error with direct API method (page {page}): {e}")
                        if "rate limit" in str(e).lower():
                            wait_time = 60
                            print(f"Rate limited. Waiting {wait_time} seconds...")
                            time.sleep(wait_time)
                            continue
                        else:
                            print("Continuing with tracks fetched so far...")
                            break
            
        except Exception as e:
            print(f"Both methods failed. Error: {e}")
            if len(scrobbles) == 0:
                raise Exception("Failed to fetch any scrobbles.")
    
    print(f"Retrieved {len(scrobbles)} scrobbles from Last.fm.")
    return scrobbles

def normalize_track_name(name):
    """Normalize track name for better comparison."""
    # Convert to lowercase
    name = name.lower()
    
    # Remove common suffixes like "(Live)", "(Remix)", etc.
    suffixes = [
        ' (live)', ' (remix)', ' (radio edit)', ' (album version)',
        ' (acoustic)', ' (remastered)', ' - live', ' - remix',
        ' - radio edit', ' - acoustic', ' - remastered'
    ]
    for suffix in suffixes:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    
    # Remove featuring artists (common formats)
    feat_indicators = [' feat. ', ' ft. ', ' featuring ', ' with ']
    for indicator in feat_indicators:
        if indicator in name:
            name = name.split(indicator)[0]
    
    # Remove special characters and extra whitespace
    name = ''.join(c for c in name if c.isalnum() or c.isspace())
    name = ' '.join(name.split())
    
    return name

def normalize_artist_name(name):
    """Normalize artist name for better comparison."""
    # Convert to lowercase
    name = name.lower()
    
    # Remove "The" prefix
    if name.startswith('the '):
        name = name[4:]
    
    # Remove special characters and extra whitespace
    name = ''.join(c for c in name if c.isalnum() or c.isspace())
    name = ' '.join(name.split())
    
    return name

def count_scrobbles(scrobbles):
    """Count how many times each track has been scrobbled."""
    scrobble_count = defaultdict(int)
    
    for scrobble in scrobbles:
        artist = normalize_artist_name(scrobble['artist'])
        title = normalize_track_name(scrobble['title'])
        key = f"{artist} - {title}"
        scrobble_count[key] += 1
    
    return scrobble_count

def analyze_tracks(liked_songs, scrobbles):
    """Compare liked songs and scrobbles to find tracks in each category."""
    # Count scrobbles
    scrobble_count = count_scrobbles(scrobbles)
    
    # Create a set of all unique scrobbled tracks
    all_scrobbled = set(scrobble_count.keys())
    
    # Create a set of all liked songs
    all_liked = set()
    for song in liked_songs:
        artist = normalize_artist_name(song['artist'])
        title = normalize_track_name(song['title'])
        key = f"{artist} - {title}"
        all_liked.add(key)
    
    # 1. Songs scrobbled but not liked
    scrobbled_not_liked = all_scrobbled - all_liked
    
    # 2i. Liked songs scrobbled once
    liked_scrobbled_once = []
    # 2ii. Liked songs never scrobbled
    liked_never_scrobbled = []
    
    for song in liked_songs:
        artist = normalize_artist_name(song['artist'])
        title = normalize_track_name(song['title'])
        key = f"{artist} - {title}"
        
        if key in scrobble_count:
            if scrobble_count[key] == 1:
                liked_scrobbled_once.append({
                    'artist': song['artist'],
                    'title': song['title'],
                    'id': song['id'],
                    'added_at': song['added_at']
                })
        else:
            liked_never_scrobbled.append({
                'artist': song['artist'],
                'title': song['title'],
                'id': song['id'],
                'added_at': song['added_at']
            })
    
    # Convert set to list and add count for scrobbled_not_liked
    scrobbled_not_liked_list = []
    for key in scrobbled_not_liked:
        artist, title = key.split(' - ', 1)
        scrobbled_not_liked_list.append({
            'artist': artist,
            'title': title,
            'scrobble_count': scrobble_count[key]
        })
    
    # Sort the lists
    scrobbled_not_liked_list.sort(key=lambda x: x['scrobble_count'], reverse=True)
    liked_scrobbled_once.sort(key=lambda x: x['added_at'], reverse=True)
    liked_never_scrobbled.sort(key=lambda x: x['added_at'], reverse=True)
    
    return {
        'scrobbled_not_liked': scrobbled_not_liked_list,
        'liked_scrobbled_once': liked_scrobbled_once,
        'liked_never_scrobbled': liked_never_scrobbled
    }

def save_results(results):
    """Save results to CSV files."""
    ensure_output_directory()
    
    # Save scrobbled but not liked songs
    with open(SCROBBLED_NOT_LIKED_FILE, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['Artist', 'Title', 'Scrobble Count'])
        for song in results['scrobbled_not_liked']:
            writer.writerow([song['artist'], song['title'], song['scrobble_count']])
    
    # Save liked but scrobbled once
    with open(LIKED_SCROBBLED_ONCE_FILE, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['Artist', 'Title', 'Spotify ID', 'Added At'])
        for song in results['liked_scrobbled_once']:
            writer.writerow([song['artist'], song['title'], song['id'], song['added_at']])
    
    # Save liked but never scrobbled
    with open(LIKED_NEVER_SCROBBLED_FILE, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['Artist', 'Title', 'Spotify ID', 'Added At'])
        for song in results['liked_never_scrobbled']:
            writer.writerow([song['artist'], song['title'], song['id'], song['added_at']])
    
    print(f"\nResults saved to {OUTPUT_DIR}/ directory.")
    print(f"1. Songs scrobbled but not liked: {len(results['scrobbled_not_liked'])}")
    print(f"2i. Liked songs scrobbled once: {len(results['liked_scrobbled_once'])}")
    print(f"2ii. Liked songs never scrobbled: {len(results['liked_never_scrobbled'])}")

def main():
    """Main function to execute the script."""
    print("Starting Spotify and Last.fm track analyzer...")
    
    # Set up API connections
    sp = setup_spotify()
    network = setup_lastfm()
    
    # Get all tracks
    liked_songs = get_all_spotify_liked_songs(sp)
    scrobbles = get_all_lastfm_scrobbles(network)
    
    # Analyze tracks
    print("\nAnalyzing tracks...")
    results = analyze_tracks(liked_songs, scrobbles)
    
    # Save results
    save_results(results)
    
    print("\nDone! You can find the results in the output directory.")

if __name__ == "__main__":
    main()