#!/usr/bin/env python3
import os
import sys
import time
import argparse
import json
from datetime import datetime
import spotipy
from pathlib import Path
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

# Load environment variables from parent directory .env file
parent_dir = Path(__file__).parent.parent
env_path = parent_dir / '.env'
load_dotenv(dotenv_path=env_path)

# Get credentials from environment variables
CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:8888/callback")

class SpotifyPlaylistManager:
    def __init__(self):
        # Set up authentication with broader scope to access liked songs
        self.scope = "user-library-read playlist-read-private playlist-modify-private playlist-modify-public"
        
        # Check for required environment variables
        if not CLIENT_ID or not CLIENT_SECRET or not REDIRECT_URI:
            print("Error: Spotify API credentials are missing")
            print("Please set up your Spotify API credentials in a .env file or environment variables")
            sys.exit(1)
        
        # Initialize Spotify client with explicit credentials
        try:
            self.sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                redirect_uri=REDIRECT_URI,
                scope=self.scope
            ))
            self.sp.current_user()  # Test the connection
            print("Successfully connected to Spotify API")
        except Exception as e:
            print(f"Error connecting to Spotify API: {e}")
            sys.exit(1)
    
    def get_liked_songs(self):
        """Get all liked songs, handling pagination for large collections"""
        print("Fetching your Liked Songs...")
        
        tracks = []
        results = self.sp.current_user_saved_tracks(limit=50)
        
        # First batch of tracks
        for item in results['items']:
            if item['track'] is not None:
                track_info = {
                    'id': item['track']['id'],
                    'name': item['track']['name'],
                    'artists': [artist['name'] for artist in item['track']['artists']],
                    'added_at': item['added_at']
                }
                tracks.append(track_info)
        
        # Handle pagination (Spotify returns max 50 liked songs per request)
        while results['next']:
            print(f"Fetched {len(tracks)} liked songs so far, getting more...")
            results = self.sp.next(results)
            for item in results['items']:
                if item['track'] is not None:
                    track_info = {
                        'id': item['track']['id'],
                        'name': item['track']['name'],
                        'artists': [artist['name'] for artist in item['track']['artists']],
                        'added_at': item['added_at']
                    }
                    tracks.append(track_info)
            
            # Add a slight delay to avoid hitting rate limits
            time.sleep(0.5)
        
        print(f"Successfully fetched {len(tracks)} liked songs")
        return tracks
    
    def get_playlist_tracks(self, playlist_id):
        """Get all tracks from a playlist, handling pagination for large playlists"""
        print(f"Fetching tracks from playlist {playlist_id}...")
        
        tracks = []
        results = self.sp.playlist_items(playlist_id, 
                                        fields='items.added_at,items.track.id,items.track.name,items.track.artists,next',
                                        additional_types=['track'])
        
        # First batch of tracks
        for item in results['items']:
            if item['track'] is not None:  # Skip None tracks (can happen with local files)
                track_info = {
                    'id': item['track']['id'],
                    'name': item['track']['name'],
                    'artists': [artist['name'] for artist in item['track']['artists']],
                    'added_at': item['added_at']
                }
                tracks.append(track_info)
        
        # Handle pagination for large playlists (Spotify returns max 100 tracks per request)
        while results['next']:
            print(f"Fetched {len(tracks)} tracks so far, getting more...")
            results = self.sp.next(results)
            for item in results['items']:
                if item['track'] is not None:
                    track_info = {
                        'id': item['track']['id'],
                        'name': item['track']['name'],
                        'artists': [artist['name'] for artist in item['track']['artists']],
                        'added_at': item['added_at']
                    }
                    tracks.append(track_info)
            
            # Add a slight delay to avoid hitting rate limits
            time.sleep(0.5)
        
        print(f"Successfully fetched {len(tracks)} tracks")
        return tracks
    
    def copy_liked_songs_to_playlist(self, target_id, order_type=None, copy_mode="bulk"):
        """
        Copy liked songs to target playlist
        
        Args:
            target_id: Target playlist ID
            order_type: Optional sorting (oldest_first, newest_first, or None for no sorting)
            copy_mode: "bulk" or "one_by_one"
        """
        # Get target playlist details
        target_info = self.sp.playlist(target_id, fields='name,owner.id')
        target_name = target_info['name']
        
        print(f"Target playlist: {target_name}")
        
        # Check user permission for target playlist
        current_user = self.sp.current_user()['id']
        target_owner = target_info['owner']['id']
        
        if current_user != target_owner:
            print("Error: You are not the owner of the target playlist.")
            print("You can only copy tracks to playlists you own.")
            return
        
        # Get all liked songs
        tracks = self.get_liked_songs()
        
        # Sort tracks if order_type is specified
        if order_type == "oldest_first":
            tracks = sorted(tracks, key=lambda x: x['added_at'])
            print("Sorting tracks: Oldest first")
        elif order_type == "newest_first":
            tracks = sorted(tracks, key=lambda x: x['added_at'], reverse=True)
            print("Sorting tracks: Newest first")
        
        # Copy tracks to target playlist
        print(f"Copy mode: {copy_mode}")
        
        if copy_mode == "bulk":
            # Add tracks in batches
            track_uris = [f"spotify:track:{track['id']}" for track in tracks]
            
            print(f"Adding {len(track_uris)} tracks in bulk to target playlist...")
            for i in range(0, len(track_uris), 100):
                batch = track_uris[i:i+100]
                print(f"Adding tracks {i+1}-{i+len(batch)}...")
                self.sp.playlist_add_items(target_id, batch)
                time.sleep(1)  # Avoid rate limits
        
        elif copy_mode == "one_by_one":
            # Add tracks one by one to preserve added date
            print(f"Adding {len(tracks)} tracks one by one to target playlist...")
            print("This may take some time. Please be patient.")
            
            for i, track in enumerate(tracks):
                track_uri = f"spotify:track:{track['id']}"
                print(f"Adding track {i+1}/{len(tracks)}: {track['name']} by {', '.join(track['artists'])}")
                self.sp.playlist_add_items(target_id, [track_uri])
                time.sleep(0.5)  # Avoid rate limits
        
        print(f"Successfully copied liked songs to {target_name}")

    def copy_playlist(self, source_id, target_id, order_type=None, copy_mode="bulk"):
        """
        Copy tracks from source playlist to target playlist
        
        Args:
            source_id: Source playlist ID
            target_id: Target playlist ID
            order_type: Optional sorting (oldest_first, newest_first, or None for no sorting)
            copy_mode: "bulk" or "one_by_one"
        """
        # Get source playlist details
        source_info = self.sp.playlist(source_id, fields='name,tracks.total')
        source_name = source_info['name']
        tracks_total = source_info['tracks']['total']
        
        print(f"Source playlist: {source_name} ({tracks_total} tracks)")
        
        # Get target playlist details
        target_info = self.sp.playlist(target_id, fields='name,owner.id')
        target_name = target_info['name']
        
        print(f"Target playlist: {target_name}")
        
        # Check user permission for target playlist
        current_user = self.sp.current_user()['id']
        target_owner = target_info['owner']['id']
        
        if current_user != target_owner:
            print("Error: You are not the owner of the target playlist.")
            print("You can only copy tracks to playlists you own.")
            return
        
        # Get all tracks from source playlist
        tracks = self.get_playlist_tracks(source_id)
        
        # Sort tracks if order_type is specified
        if order_type == "oldest_first":
            tracks = sorted(tracks, key=lambda x: x['added_at'])
            print("Sorting tracks: Oldest first")
        elif order_type == "newest_first":
            tracks = sorted(tracks, key=lambda x: x['added_at'], reverse=True)
            print("Sorting tracks: Newest first")
        
        # Copy tracks to target playlist
        print(f"Copy mode: {copy_mode}")
        
        if copy_mode == "bulk":
            # Add tracks in batches
            track_uris = [f"spotify:track:{track['id']}" for track in tracks]
            
            print(f"Adding {len(track_uris)} tracks in bulk to target playlist...")
            for i in range(0, len(track_uris), 100):
                batch = track_uris[i:i+100]
                print(f"Adding tracks {i+1}-{i+len(batch)}...")
                self.sp.playlist_add_items(target_id, batch)
                time.sleep(1)  # Avoid rate limits
        
        elif copy_mode == "one_by_one":
            # Add tracks one by one to preserve added date
            print(f"Adding {len(tracks)} tracks one by one to target playlist...")
            print("This may take some time. Please be patient.")
            
            for i, track in enumerate(tracks):
                track_uri = f"spotify:track:{track['id']}"
                print(f"Adding track {i+1}/{len(tracks)}: {track['name']} by {', '.join(track['artists'])}")
                self.sp.playlist_add_items(target_id, [track_uri])
                time.sleep(0.5)  # Avoid rate limits
        
        print(f"Successfully copied tracks from {source_name} to {target_name}")
    
    def reorder_playlist(self, playlist_id, order_type, target_playlist_id=None):
        """
        Reorder a playlist based on the specified order type
        
        Args:
            playlist_id: Source playlist ID (or "liked" for Liked Songs)
            order_type: Sorting method (oldest_first or newest_first)
            target_playlist_id: Optional target playlist ID for copying
        """
        # Handle Liked Songs
        if playlist_id.lower() == "liked":
            if not target_playlist_id:
                print("Error: You need to specify a target playlist when using Liked Songs as the source.")
                print("Liked Songs cannot be directly modified.")
                return
            
            copy_mode = input("Copy mode - bulk or one_by_one? [bulk]: ").lower() or "bulk"
            if copy_mode not in ["bulk", "one_by_one"]:
                print("Invalid copy mode. Using 'bulk' by default.")
                copy_mode = "bulk"
            
            self.copy_liked_songs_to_playlist(target_playlist_id, order_type, copy_mode)
            return
        
        # Handle regular playlist
        # If target_playlist_id is provided, copy tracks to target playlist
        if target_playlist_id:
            copy_mode = input("Copy mode - bulk or one_by_one? [bulk]: ").lower() or "bulk"
            if copy_mode not in ["bulk", "one_by_one"]:
                print("Invalid copy mode. Using 'bulk' by default.")
                copy_mode = "bulk"
            
            self.copy_playlist(playlist_id, target_playlist_id, order_type, copy_mode)
            return
        
        # Otherwise, reorder the original playlist (original functionality)
        # Get playlist details
        playlist_info = self.sp.playlist(playlist_id, fields='name,owner.id,tracks.total')
        playlist_name = playlist_info['name']
        tracks_total = playlist_info['tracks']['total']
        
        print(f"Working with playlist: {playlist_name} ({tracks_total} tracks)")
        
        # Check user permission
        current_user = self.sp.current_user()['id']
        playlist_owner = playlist_info['owner']['id']
        
        if current_user != playlist_owner:
            print("Warning: You are not the owner of this playlist.")
            print("You can create a new sorted playlist, but cannot modify the original.")
            choice = input("Create a new playlist with sorted tracks? (y/n): ")
            if choice.lower() != 'y':
                return
            
            # Create a new playlist
            new_playlist_name = f"{playlist_name} - Sorted ({order_type})"
            new_playlist = self.sp.user_playlist_create(
                current_user, 
                new_playlist_name, 
                public=False, 
                description=f"Sorted version of {playlist_name} ({order_type})"
            )
            target_playlist_id = new_playlist['id']
            print(f"Created new playlist: {new_playlist_name}")
        else:
            # Use existing playlist
            target_playlist_id = playlist_id
        
        # Get all tracks
        tracks = self.get_playlist_tracks(playlist_id)
        
        # Sort tracks by added_at date
        if order_type == "oldest_first":
            sorted_tracks = sorted(tracks, key=lambda x: x['added_at'])
            print("Sorting tracks: Oldest first")
        elif order_type == "newest_first":
            sorted_tracks = sorted(tracks, key=lambda x: x['added_at'], reverse=True)
            print("Sorting tracks: Newest first")
        else:
            print(f"Unknown order type: {order_type}")
            return
        
        # If creating a new playlist, add all tracks in the sorted order
        if target_playlist_id != playlist_id:
            # Add tracks in batches to avoid API limits (max 100 per request)
            track_uris = [f"spotify:track:{track['id']}" for track in sorted_tracks]
            
            for i in range(0, len(track_uris), 100):
                batch = track_uris[i:i+100]
                print(f"Adding tracks {i+1}-{i+len(batch)} to new playlist...")
                self.sp.playlist_add_items(target_playlist_id, batch)
                time.sleep(1)  # Avoid rate limits
            
            print(f"Successfully created sorted playlist: {new_playlist_name}")
            print(f"New playlist ID: {target_playlist_id}")
            return
        
        # For existing playlist, we need to reorder tracks
        # This is complex due to Spotify API limitations:
        # 1. We can't reorder the entire playlist at once
        # 2. We need to handle the reordering in chunks
        
        print("Reordering existing playlist...")
        print("Warning: This process may take some time for large playlists")
        
        # Get current track order
        current_track_ids = [track['id'] for track in tracks]
        target_track_ids = [track['id'] for track in sorted_tracks]
        
        # If already in the correct order, no need to reorder
        if current_track_ids == target_track_ids:
            print("Playlist is already in the requested order!")
            return
        
        # For simplicity, we'll remove all tracks and add them back in the correct order
        # Note: This approach preserves the playlist but temporarily empties it
        print("This will temporarily remove all tracks and then add them back in sorted order.")
        confirm = input("Continue? (y/n): ")
        if confirm.lower() != 'y':
            return
        
        # Remove all tracks in batches
        print("Removing all tracks from playlist...")
        track_uris = [f"spotify:track:{track_id}" for track_id in current_track_ids]
        
        for i in range(0, len(track_uris), 100):
            batch = track_uris[i:i+100]
            print(f"Removing tracks {i+1}-{i+len(batch)}...")
            self.sp.playlist_remove_all_occurrences_of_items(playlist_id, batch)
            time.sleep(1)  # Avoid rate limits
        
        # Add tracks back in sorted order
        print("Adding tracks in sorted order...")
        sorted_uris = [f"spotify:track:{track['id']}" for track in sorted_tracks]
        
        for i in range(0, len(sorted_uris), 100):
            batch = sorted_uris[i:i+100]
            print(f"Adding tracks {i+1}-{i+len(batch)}...")
            self.sp.playlist_add_items(playlist_id, batch)
            time.sleep(1)  # Avoid rate limits
        
        print(f"Successfully reordered playlist: {playlist_name}")

def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Spotify Playlist Manager")
    parser.add_argument("--playlist", "-p", required=True, 
                       help="Source playlist ID (or 'liked' for Liked Songs)")
    parser.add_argument("--order", "-o", choices=["oldest_first", "newest_first"], 
                        default="oldest_first", help="Order type (default: oldest_first)")
    parser.add_argument("--target", "-t", help="Target playlist ID for copying (optional)")
    parser.add_argument("--copy-mode", "-c", choices=["bulk", "one_by_one"], 
                        default="bulk", help="Copy mode: bulk or one-by-one (default: bulk)")
    
    args = parser.parse_args()
    
    # Initialize and run
    manager = SpotifyPlaylistManager()
    manager.reorder_playlist(args.playlist, args.order, args.target)

if __name__ == "__main__":
    main()