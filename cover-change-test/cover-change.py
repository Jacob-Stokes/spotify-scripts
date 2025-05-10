#!/usr/bin/env python3
"""
Four-Phase Playlist Cover Changer

This script:
1. Changes the cover image of a Spotify playlist based on time of day
2. Uses four different images: morning, day, evening, and night
3. Schedules precise transitions at calculated times
4. Uses sunrise/sunset data for accurate seasonal changes

Usage:
    python cover_changer.py
    python cover_changer.py --debug "06:00,09:00,18:00,21:00"
"""

import os
import time as sleep_module
import base64
import datetime
import ssl
import certifi
import requests
import spotipy
import argparse
from spotipy.oauth2 import SpotifyOAuth
from pathlib import Path
from dotenv import load_dotenv
import logging
import json
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('time_cover_changer.log')
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
parser = argparse.ArgumentParser(description="Four-Phase Playlist Cover Changer")
parser.add_argument("--debug", metavar="times", type=str, 
                   help="Comma-separated list of times for debug mode (morning,day,evening,night)")
args = parser.parse_args()

# ====== CONFIGURATION ======
# Spotify API settings
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:8888/callback")
SPOTIFY_SCOPE = "ugc-image-upload playlist-modify-public playlist-modify-private"

# Cover image settings
PLAYLIST_ID = os.getenv("COVER_CHANGE_PLAYLIST_ID")
MORNING_IMAGE_PATH = os.getenv("MORNING_IMAGE_PATH", "images/morning.jpg")
DAY_IMAGE_PATH = os.getenv("DAY_IMAGE_PATH", "images/day.jpg")
EVENING_IMAGE_PATH = os.getenv("EVENING_IMAGE_PATH", "images/evening.jpg")
NIGHT_IMAGE_PATH = os.getenv("NIGHT_IMAGE_PATH", "images/night.jpg")

# Additional time settings
# How long after sunrise morning ends (in hours)
MORNING_DURATION = float(os.getenv("MORNING_DURATION", "3"))
# How long before sunset evening starts (in hours)
EVENING_DURATION = float(os.getenv("EVENING_DURATION", "2"))

# Timezone adjustment (for servers running in UTC but calculating times for BST/local timezone)
# Set to 1 for BST (summer time), 0 for GMT (winter time)
TIME_OFFSET = float(os.getenv("TIME_OFFSET", "1"))

# Location settings (London)
LATITUDE = 51.5074
LONGITUDE = -0.1278

# Debug mode
DEBUG_MODE = args.debug is not None
DEBUG_TIMES = None
if DEBUG_MODE:
    try:
        time_strings = args.debug.split(',')
        if len(time_strings) != 4:
            raise ValueError("Debug mode requires exactly 4 times (morning,day,evening,night)")
            
        # Parse the time strings
        DEBUG_TIMES = {}
        phases = ['morning', 'day', 'evening', 'night']
        
        for i, phase in enumerate(phases):
            time_str = time_strings[i].strip()
            hours, minutes = map(int, time_str.split(':'))
            today = datetime.datetime.today().date()
            debug_time = datetime.datetime.combine(today, datetime.time(hours, minutes))
            
            # If the time has already passed today, schedule for tomorrow
            if debug_time < datetime.datetime.now():
                debug_time = datetime.datetime.combine(today + datetime.timedelta(days=1), 
                                                     datetime.time(hours, minutes))
                
            DEBUG_TIMES[phase] = debug_time
            
        logger.info("DEBUG MODE ENABLED with custom times:")
        for phase, time in DEBUG_TIMES.items():
            logger.info(f"  {phase.capitalize()}: {time.strftime('%H:%M')}")
            
    except Exception as e:
        logger.error(f"Error parsing debug times: {e}")
        logger.error("Format should be: --debug \"06:00,09:00,18:00,21:00\"")
        logger.error("Using normal sunrise/sunset calculations instead")
        DEBUG_MODE = False

# State file
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cover_state.json')

# Convert relative image paths to absolute paths if needed
script_dir = Path(__file__).parent.absolute()
if not os.path.isabs(MORNING_IMAGE_PATH):
    MORNING_IMAGE_PATH = os.path.join(script_dir, MORNING_IMAGE_PATH)
if not os.path.isabs(DAY_IMAGE_PATH):
    DAY_IMAGE_PATH = os.path.join(script_dir, DAY_IMAGE_PATH)
if not os.path.isabs(EVENING_IMAGE_PATH):
    EVENING_IMAGE_PATH = os.path.join(script_dir, EVENING_IMAGE_PATH)
if not os.path.isabs(NIGHT_IMAGE_PATH):
    NIGHT_IMAGE_PATH = os.path.join(script_dir, NIGHT_IMAGE_PATH)

logger.info(f"Morning image path: {MORNING_IMAGE_PATH}")
logger.info(f"Day image path: {DAY_IMAGE_PATH}")
logger.info(f"Evening image path: {EVENING_IMAGE_PATH}")
logger.info(f"Night image path: {NIGHT_IMAGE_PATH}")

# Show timezone configuration
if TIME_OFFSET > 0:
    logger.info(f"Time offset: +{TIME_OFFSET} hours (adjusting for BST/summer time)")
elif TIME_OFFSET < 0:
    logger.info(f"Time offset: {TIME_OFFSET} hours")
else:
    logger.info("No time offset applied (using server timezone directly)")

# Fail early if required env vars are missing
required_vars = [
    "SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "COVER_CHANGE_PLAYLIST_ID"
]

# Check using os.getenv() instead of locals()
missing_vars = [var for var in required_vars if not os.getenv(var)]
if missing_vars:
    raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

# Check if image files exist
for img_path, img_name in [
    (MORNING_IMAGE_PATH, "Morning"), 
    (DAY_IMAGE_PATH, "Day"),
    (EVENING_IMAGE_PATH, "Evening"),
    (NIGHT_IMAGE_PATH, "Night")
]:
    if not os.path.exists(img_path):
        raise FileNotFoundError(f"{img_name} image file not found: {img_path}")

# Initialize Spotify client (will be initialized when needed)
sp = None

# ====== HELPER FUNCTIONS ======
def get_now_with_tzinfo():
    """Get current datetime with timezone info"""
    return datetime.datetime.now().astimezone()

def ensure_timezone_aware(dt):
    """Ensure a datetime object has timezone info"""
    if dt.tzinfo is None:
        return dt.astimezone()  # Use local timezone
    return dt

def ensure_timezone_consistency(dt1, dt2):
    """Ensure both datetime objects have consistent timezone info"""
    if dt1.tzinfo is not None and dt2.tzinfo is None:
        dt2 = dt2.replace(tzinfo=dt1.tzinfo)
    elif dt1.tzinfo is None and dt2.tzinfo is not None:
        dt1 = dt1.replace(tzinfo=dt2.tzinfo)
    return dt1, dt2

# ====== SCHEDULER EVENT HANDLERS ======
def job_executed_event(event):
    """Log when a job is successfully executed"""
    job = scheduler.get_job(event.job_id)
    logger.info(f"Job executed successfully: {event.job_id}, scheduled run time: {job.next_run_time if job else 'Unknown'}")

def job_error_event(event):
    """Log when a job has an error"""
    job = scheduler.get_job(event.job_id)
    logger.error(f"Job error: {event.job_id}, scheduled run time: {job.next_run_time if job else 'Unknown'}")
    logger.error(f"Exception: {event.exception}")
    logger.error(f"Traceback: {event.traceback}")

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

# ====== TIME CALCULATIONS ======
def get_sun_times():
    """
    Get today's sunrise and sunset times for London
    
    Returns:
        tuple: (sunrise, sunset) as datetime objects (in local time)
    """
    # Option 1: Use Sunrise-Sunset API
    try:
        url = f"https://api.sunrise-sunset.org/json?lat={LATITUDE}&lng={LONGITUDE}&formatted=0"
        response = requests.get(url)
        data = response.json()
        
        if response.status_code == 200 and data['status'] == 'OK':
            # Convert sunrise/sunset UTC times to local time
            sunrise_utc = datetime.datetime.fromisoformat(data['results']['sunrise'].replace('Z', '+00:00'))
            sunset_utc = datetime.datetime.fromisoformat(data['results']['sunset'].replace('Z', '+00:00'))
            
            local_timezone = datetime.datetime.now().astimezone().tzinfo
            sunrise_local = sunrise_utc.astimezone(local_timezone)
            sunset_local = sunset_utc.astimezone(local_timezone)
            
            # Apply configured time offset
            if TIME_OFFSET != 0:
                offset = datetime.timedelta(hours=TIME_OFFSET)
                sunrise_local = sunrise_local + offset
                sunset_local = sunset_local + offset
                logger.info(f"Applied time offset adjustment ({TIME_OFFSET:+.1f} hours) to sunrise/sunset times")
            
            logger.info(f"Today's sunrise in London: {sunrise_local.strftime('%H:%M')}")
            logger.info(f"Today's sunset in London: {sunset_local.strftime('%H:%M')}")
            
            return sunrise_local, sunset_local
    except Exception as e:
        logger.error(f"Error fetching sun times from API: {e}")
    
    # Option 2: Fallback to simple calculation (approximate sunrise/sunset times for London)
    month = datetime.datetime.now().month
    
    # Approximate times by month (24-hour format)
    sun_times = {
        # month: (sunrise, sunset)
        1: ("08:00", "16:00"),  # January
        2: ("07:30", "17:00"),  # February
        3: ("06:30", "18:00"),  # March
        4: ("06:00", "19:30"),  # April
        5: ("05:00", "20:30"),  # May
        6: ("04:30", "21:00"),  # June
        7: ("05:00", "21:00"),  # July
        8: ("05:30", "20:00"),  # August
        9: ("06:30", "19:00"),  # September
        10: ("07:00", "17:30"), # October
        11: ("07:30", "16:00"), # November
        12: ("08:00", "15:45"), # December
    }
    
    today = datetime.datetime.now().date()
    sunrise_time_str, sunset_time_str = sun_times[month]
    
    sunrise_hours, sunrise_minutes = map(int, sunrise_time_str.split(':'))
    sunset_hours, sunset_minutes = map(int, sunset_time_str.split(':'))
    
    # Make times timezone-aware to avoid comparison issues
    local_timezone = datetime.datetime.now().astimezone().tzinfo
    sunrise_time = datetime.datetime.combine(today, datetime.time(sunrise_hours, sunrise_minutes))
    sunset_time = datetime.datetime.combine(today, datetime.time(sunset_hours, sunset_minutes))
    
    # Make them timezone-aware
    sunrise_time = sunrise_time.replace(tzinfo=local_timezone)
    sunset_time = sunset_time.replace(tzinfo=local_timezone)
    
    # Apply configured time offset for fallback times too
    if TIME_OFFSET != 0:
        offset = datetime.timedelta(hours=TIME_OFFSET)
        sunrise_time = sunrise_time + offset
        sunset_time = sunset_time + offset
        logger.info(f"Applied time offset adjustment ({TIME_OFFSET:+.1f} hours) to fallback sunrise/sunset times")
    
    logger.info(f"Using fallback sunrise time for London: {sunrise_time.strftime('%H:%M')}")
    logger.info(f"Using fallback sunset time for London: {sunset_time.strftime('%H:%M')}")
    
    return sunrise_time, sunset_time

def calculate_phase_times():
    """
    Calculate the times for all four phases
    
    Returns:
        dict: Dictionary with start times for each phase
    """
    # If in debug mode, use the provided times
    if DEBUG_MODE and DEBUG_TIMES:
        # Apply the time offset to debug times if needed
        if TIME_OFFSET != 0 and not DEBUG_MODE:
            offset = datetime.timedelta(hours=TIME_OFFSET)
            aware_times = {}
            for phase, time in DEBUG_TIMES.items():
                aware_time = ensure_timezone_aware(time)
                aware_times[phase] = aware_time + offset
            return aware_times
        else:
            # Make sure debug times are timezone-aware
            aware_times = {}
            for phase, time in DEBUG_TIMES.items():
                aware_times[phase] = ensure_timezone_aware(time)
            return aware_times
        
    # Otherwise, calculate real times based on sunrise/sunset
    sunrise, sunset = get_sun_times()
    
    # Calculate phase transition times
    morning_start = sunrise
    day_start = sunrise + datetime.timedelta(hours=MORNING_DURATION)
    evening_start = sunset - datetime.timedelta(hours=EVENING_DURATION)
    night_start = sunset
    
    return {
        'morning': morning_start,
        'day': day_start,
        'evening': evening_start,
        'night': night_start
    }

def get_current_phase():
    """
    Determine the current phase based on time
    
    Returns:
        str: 'morning', 'day', 'evening', or 'night'
    """
    now = get_now_with_tzinfo()
    phase_times = calculate_phase_times()
    
    # Determine current phase
    if now < phase_times['morning']:
        return 'night'  # Before sunrise, it's still night
    elif now < phase_times['day']:
        return 'morning'
    elif now < phase_times['evening']:
        return 'day'
    elif now < phase_times['night']:
        return 'evening'
    else:
        return 'night'

def calculate_times_for_tomorrow():
    """Calculate times for tomorrow and schedule the changes"""
    # Clear any existing jobs
    scheduler.remove_all_jobs()
    
    # Get tomorrow's date
    now = get_now_with_tzinfo()
    tomorrow = now.date() + datetime.timedelta(days=1)
    logger.info(f"Calculating times for tomorrow ({tomorrow})")
    
    # Calculate tomorrow's phase times
    if DEBUG_MODE and DEBUG_TIMES:
        # In debug mode, add one day to all times
        tomorrow_phases = {}
        for phase, time in DEBUG_TIMES.items():
            aware_time = ensure_timezone_aware(time)
            tomorrow_phases[phase] = aware_time + datetime.timedelta(days=1)
            
        # Apply time offset if configured
        if TIME_OFFSET != 0 and not DEBUG_MODE:
            offset = datetime.timedelta(hours=TIME_OFFSET)
            for phase in tomorrow_phases:
                tomorrow_phases[phase] = tomorrow_phases[phase] + offset
    else:
        # Calculate real times based on sunrise/sunset
        today_phases = calculate_phase_times()
        
        # Create tomorrow's phase times
        tomorrow_phases = {}
        for phase, time in today_phases.items():
            tomorrow_time = datetime.datetime.combine(tomorrow, time.time())
            tomorrow_phases[phase] = tomorrow_time.replace(tzinfo=time.tzinfo)
    
    # Schedule cover changes
    schedule_phase_changes(tomorrow_phases)
    
    # Also schedule the recalculation for tomorrow night
    recalculation_time = datetime.datetime.combine(tomorrow, datetime.time(23, 0))
    recalculation_time = recalculation_time.replace(tzinfo=now.tzinfo)
        
    scheduler.add_job(
        calculate_times_for_tomorrow, 
        'date', 
        run_date=recalculation_time, 
        id='recalculate',
        replace_existing=True,
        misfire_grace_time=3600  # Allow job to run up to 1 hour late
    )
    
    # Log all scheduled times
    for phase, time in tomorrow_phases.items():
        logger.info(f"Scheduled {phase} cover change for: {time.strftime('%Y-%m-%d %H:%M')}")
    logger.info(f"Scheduled next calculation for: {recalculation_time.strftime('%Y-%m-%d %H:%M')}")

def schedule_phase_changes(phase_times):
    """Schedule phase changes using the provided times"""
    phases = ['morning', 'day', 'evening', 'night']
    now = get_now_with_tzinfo()
    
    for phase in phases:
        time = phase_times[phase]
        
        # Ensure the time is timezone-aware
        time = ensure_timezone_aware(time)
        
        # Only schedule if the time is in the future
        if time > now:
            logger.info(f"Scheduling {phase} cover change for {time.strftime('%Y-%m-%d %H:%M')}")
            
            # Use a function instead of a lambda to avoid closure issues
            scheduler.add_job(
                change_cover_job,
                'date', 
                run_date=time, 
                id=phase,
                args=[phase],
                replace_existing=True,
                misfire_grace_time=3600  # Allow job to run up to 1 hour late
            )
        else:
            logger.info(f"Skipping scheduling {phase} cover change as time {time.strftime('%H:%M')} has already passed")

def change_cover_job(phase):
    """Job function to change cover to the specified phase"""
    logger.info(f"Scheduled job running: change to {phase} cover")
    change_cover(phase)

def schedule_today_changes():
    """Schedule changes for today based on current time"""
    phase_times = calculate_phase_times()
    
    # Schedule remaining changes for today
    schedule_phase_changes(phase_times)
    
    # Always schedule tomorrow's calculation
    now = get_now_with_tzinfo()
    tomorrow = now.date() + datetime.timedelta(days=1)
    recalculation_time = datetime.datetime.combine(tomorrow, datetime.time(0, 1))  # Just after midnight
    recalculation_time = recalculation_time.replace(tzinfo=now.tzinfo)
    
    scheduler.add_job(
        calculate_times_for_tomorrow, 
        'date', 
        run_date=recalculation_time, 
        id='recalculate',
        replace_existing=True,
        misfire_grace_time=3600  # Allow job to run up to 1 hour late
    )
    
    logger.info(f"Scheduled next calculation for: {recalculation_time.strftime('%Y-%m-%d %H:%M')}")

# ====== IMAGE HELPERS ======
def resize_image_if_needed(image_path, max_size_kb=190):
    """
    Resize an image if it's too large for Spotify
    
    Args:
        image_path (str): Path to the image file
        max_size_kb (int): Maximum size in KB
        
    Returns:
        bytes: Image data
    """
    # Get original file size
    original_size_kb = os.path.getsize(image_path) / 1024
    
    # If file is already small enough, return it as is
    if original_size_kb <= max_size_kb:
        with open(image_path, "rb") as f:
            return f.read()
    
    # If file is too large and PIL is available, resize it
    try:
        from PIL import Image
        import io
        
        logger.info(f"Image is too large ({original_size_kb:.2f} KB), resizing to target {max_size_kb} KB")
        
        # Open the image
        img = Image.open(image_path)
        
        # Start with original quality
        quality = 95
        
        # Iteratively reduce quality until we're under the limit
        while True:
            img_byte_arr = io.BytesIO()
            img.save(img_byte_arr, format="JPEG", quality=quality)
            
            img_data = img_byte_arr.getvalue()
            current_size_kb = len(img_data) / 1024
            
            if current_size_kb <= max_size_kb:
                logger.info(f"Resized image to {current_size_kb:.2f} KB with quality={quality}")
                return img_data
            
            quality -= 5
            if quality < 70:
                # If we can't get it small enough with quality, resize dimensions
                width, height = img.size
                img = img.resize((int(width * 0.9), int(height * 0.9)), Image.LANCZOS)
                quality = 85
    except ImportError:
        logger.warning("PIL not installed, can't resize image. Using original size.")
        with open(image_path, "rb") as f:
            return f.read()
    except Exception as e:
        logger.error(f"Error resizing image: {e}")
        with open(image_path, "rb") as f:
            return f.read()

def encode_image_base64(image_path):
    """
    Read an image file and encode it as base64
    
    Args:
        image_path (str): Path to the image file
        
    Returns:
        str: Base64-encoded image data
    """
    try:
        # Get image data, resizing if needed
        image_data = resize_image_if_needed(image_path)
        file_size_kb = len(image_data) / 1024
        logger.info(f"Image size: {file_size_kb:.2f} KB")
        
        # Encode image data as base64
        encoded_image = base64.b64encode(image_data).decode("utf-8")
        encoded_size_kb = len(encoded_image) / 1024
        logger.info(f"Successfully encoded image (base64 length: {len(encoded_image)}, size: {encoded_size_kb:.2f} KB)")
        
        return encoded_image
    except Exception as e:
        logger.error(f"Error encoding image: {e}")
        raise

def change_playlist_cover(playlist_id, image_path):
    """
    Change the cover image of a playlist
    
    Args:
        playlist_id (str): Spotify playlist ID
        image_path (str): Path to the image file
        
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Initialize Spotify if needed
        initialize_spotify()
        
        # Get playlist info for display
        playlist_info = sp.playlist(playlist_id, fields='name')
        playlist_name = playlist_info['name']
        
        logger.info(f"Changing cover for playlist '{playlist_name}' using {image_path}")
        
        # Get image data
        encoded_image = encode_image_base64(image_path)
        
        # Update playlist cover
        logger.info("Uploading cover image to Spotify...")
        sp.playlist_upload_cover_image(playlist_id, encoded_image)
        
        logger.info(f"Successfully updated cover image for playlist '{playlist_name}'")
        return True
    except Exception as e:
        logger.error(f"Error changing playlist cover: {e}")
        return False

# ====== STATE MANAGEMENT ======
def save_state(phase):
    """Save current state to file"""
    try:
        # Calculate all phase times for reference
        phase_times = calculate_phase_times()
        times_iso = {p: t.isoformat() for p, t in phase_times.items()}
        
        data = {
            'phase': phase,
            'timestamp': datetime.datetime.now().isoformat(),
            'playlist_id': PLAYLIST_ID,
            'phase_times': times_iso,
            'debug_mode': DEBUG_MODE,
            'time_offset': TIME_OFFSET
        }
        
        with open(STATE_FILE, 'w') as f:
            json.dump(data, f, indent=2)
            
        logger.info(f"Saved state: {phase}")
    except Exception as e:
        logger.error(f"Error saving state: {e}")

def load_state():
    """Load state from file"""
    if not os.path.exists(STATE_FILE):
        return None
        
    try:
        with open(STATE_FILE, 'r') as f:
            data = json.load(f)
            
        logger.info(f"Loaded state: {data['phase']} (set at {data['timestamp']})")
        return data['phase']
    except Exception as e:
        logger.error(f"Error loading state: {e}")
        return None

# ====== COVER CHANGE FUNCTIONS ======
def get_image_path(phase):
    """Get the image path for a given phase"""
    if phase == 'morning':
        return MORNING_IMAGE_PATH
    elif phase == 'day':
        return DAY_IMAGE_PATH
    elif phase == 'evening':
        return EVENING_IMAGE_PATH
    elif phase == 'night':
        return NIGHT_IMAGE_PATH
    else:
        raise ValueError(f"Invalid phase: {phase}")

def change_cover(phase):
    """Change cover to specified phase"""
    logger.info(f"Changing to {phase.upper()} cover")
    
    # Check current state
    current_state = load_state()
    if current_state == phase:
        logger.info(f"Already using {phase} cover, no change needed")
        return
    
    # Get image path
    image_path = get_image_path(phase)
    
    # Change cover
    success = change_playlist_cover(PLAYLIST_ID, image_path)
    
    if success:
        save_state(phase)

def set_initial_cover():
    """Set the initial cover based on current time"""
    # Check what phase it currently is
    current_phase = get_current_phase()
    logger.info(f"Current phase: {current_phase}")
    
    # Update cover
    change_cover(current_phase)

# ====== MAIN FUNCTION ======
def main():
    """Main function"""
    logger.info("\n" + "="*50)
    logger.info("Starting Four-Phase Playlist Cover Changer")
    logger.info(f"Target Playlist ID: {PLAYLIST_ID}")
    
    # Log configuration details
    if DEBUG_MODE:
        logger.info("RUNNING IN DEBUG MODE with custom times")
    else:
        logger.info(f"Location: London (Latitude: {LATITUDE}, Longitude: {LONGITUDE})")
        logger.info(f"Morning Duration: {MORNING_DURATION} hours after sunrise")
        logger.info(f"Evening Duration: {EVENING_DURATION} hours before sunset")
        
    if TIME_OFFSET != 0:
        logger.info(f"Time offset: {TIME_OFFSET:+.1f} hours (adjusting for timezone differences)")
        if TIME_OFFSET == 1:
            logger.info("This offset is likely compensating for BST (summer time) while server runs on UTC")
    logger.info("="*50 + "\n")
    
    # Set up scheduler event handlers for better debugging
    scheduler.add_listener(job_executed_event, EVENT_JOB_EXECUTED)
    scheduler.add_listener(job_error_event, EVENT_JOB_ERROR)
    
    # Initialize Spotify
    initialize_spotify()
    
    # Get playlist info for better display
    try:
        playlist_info = sp.playlist(PLAYLIST_ID, fields='name,owner(display_name)')
        playlist_name = playlist_info['name']
        playlist_owner = playlist_info['owner']['display_name']
        logger.info(f"Target Playlist: '{playlist_name}' (owned by {playlist_owner})")
    except Exception as e:
        logger.error(f"Could not fetch playlist details: {e}")
    
    # Calculate and display phase times
    phase_times = calculate_phase_times()
    logger.info("Today's phase transition times:")
    for phase, time in phase_times.items():
        logger.info(f"  {phase.capitalize()}: {time.strftime('%H:%M')}")
    
    # Set initial cover based on current time
    set_initial_cover()
    
    # Schedule changes for today and tomorrow
    schedule_today_changes()
    
    # Start the scheduler
    logger.info("Starting scheduler")
    scheduler.start()
    
    # List all scheduled jobs
    jobs = scheduler.get_jobs()
    logger.info(f"Scheduled jobs ({len(jobs)}):")
    for job in jobs:
        logger.info(f"  Job ID: {job.id}, Next run: {job.next_run_time}")
    
    # Keep the script running - use sleep_module to avoid any namespace issues
    try:
        while True:
            sleep_module.sleep(60)  # Sleep for 60 seconds
    except KeyboardInterrupt:
        logger.info("Script terminated by user. Shutting down scheduler...")
        scheduler.shutdown()
        logger.info("Bye!")

# Initialize the scheduler
scheduler = BackgroundScheduler(misfire_grace_time=3600)  # Allow jobs to run up to 1 hour late

if __name__ == "__main__":
    main()