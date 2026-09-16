import csv
import logging
import os
import random
import re
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import yt_dlp
except ImportError:  # pragma: no cover - only used when dependency is not installed
    yt_dlp = None


# Set up Colored Logging without external dependencies
class ColoredFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": "\033[94m",  # Blue
        "INFO": "\033[92m",  # Green
        "WARNING": "\033[93m",  # Yellow
        "ERROR": "\033[91m",  # Red
        "CRITICAL": "\033[95m\033[1m",  # Bold Magenta
    }
    RESET = "\033[0m"

    def format(self, record):
        color = self.COLORS.get(record.levelname, self.RESET)
        # Apply color to the whole message or just log level
        formatted_message = super().format(record)
        return f"{color}{formatted_message}{self.RESET}"


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
# Prevent duplicate logs if handler exists
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = ColoredFormatter("%(asctime)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# Configuration File settings
CONFIG_FILE = "options.json"
DEFAULT_CONFIG = {"random_order": False, "threads": 6}


def load_config():
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=4)
        logger.info(f"Created default options file: {CONFIG_FILE}")
        return DEFAULT_CONFIG

    with open(CONFIG_FILE, "r") as f:
        try:
            config = json.load(f)
            # Ensure missing default keys are filled
            for key, value in DEFAULT_CONFIG.items():
                if key not in config:
                    config[key] = value
            return config
        except json.JSONDecodeError:
            logger.error(f"Failed to parse {CONFIG_FILE}. Using default settings.")
            return DEFAULT_CONFIG


def create_out_dir():
    out_dir = "out"
    if not os.path.exists(out_dir):
        os.makedirs(out_dir)
        logger.info(f"Created directory: '{out_dir}/'")
    return out_dir


def sanitize_filename_component(value):
    if value is None:
        return "Unknown"

    cleaned = str(value)
    cleaned = cleaned.replace("/", " - ")
    cleaned = cleaned.replace("\\", " - ")
    cleaned = cleaned.replace(":", " - ")
    cleaned = cleaned.replace("*", " ")
    cleaned = cleaned.replace("?", "")
    cleaned = cleaned.replace('"', "")
    cleaned = cleaned.replace("<", " ")
    cleaned = cleaned.replace(">", " ")
    cleaned = cleaned.replace("|", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")
    return cleaned or "Unknown"


def build_output_basename(track_name, artist_names):
    track = sanitize_filename_component(track_name)
    artist_names_clean = artist_names.replace(";", ", ") if artist_names else ""
    artists = sanitize_filename_component(artist_names_clean)

    if artists and artists != track:
        return f"{track} - {artists}"
    return track


def get_unique_output_base_name(out_dir, track_name, artist_names):
    base_name = build_output_basename(track_name, artist_names)
    candidate = base_name
    counter = 2

    while any(
        os.path.exists(os.path.join(out_dir, f"{candidate}.{ext}"))
        for ext in ("mp3", "webm", "m4a", "aac", "wav", "flac")
    ):
        candidate = f"{base_name} ({counter})"
        counter += 1

    return candidate


def download_song(track_name, artist_names, out_dir):
    if yt_dlp is None:
        raise RuntimeError("yt_dlp is not installed. Please run: pip install -r requirements.txt")

    artist_names_text = artist_names or ""
    output_base_name = get_unique_output_base_name(out_dir, track_name, artist_names_text)
    query = f"{track_name} {artist_names_text}".strip()
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(out_dir, f"{output_base_name}.%(ext)s"),
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
        "quiet": False,
        "no_warnings": False,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 5,
        "skip_unavailable_fragments": True,
        "ignoreerrors": False,
        "default_search": "ytsearch",
        "extract_flat": False,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            logger.info(f"Searching and downloading: '{query}'")
            ydl.download([f"ytsearch1:{query}"])
            logger.info(f"Successfully downloaded: '{output_base_name}'")
            return True
        except Exception as e:
            logger.error(f"Failed to download '{query}': {e}")
            return False


def main():
    logger.info("Starting Spotify Playlist to MP3 conversion process")

    config = load_config()
    out_dir = create_out_dir()
    csv_file = "data.csv"

    if not os.path.exists(csv_file):
        logger.error(f"CSV file not found: {csv_file}")
        return

    songs_processed = 0
    songs_failed = 0

    with open(csv_file, mode="r", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        rows = [row for row in reader if row.get("Track Name")]

    if not rows:
        logger.warning(f"No valid tracks found in {csv_file}.")
        return

    if config.get("random_order", False):
        logger.info("Random order enabled. Shuffling tracks...")
        random.shuffle(rows)

    tracks = []
    for row in rows:
        track_name = row.get("Track Name")
        artist_names = row.get("Artist Name(s)")
        artist_names_clean = artist_names.replace(";", ", ") if artist_names else ""
        tracks.append((track_name, artist_names_clean))

    max_threads = max(1, config.get("threads", 6))
    logger.info(f"Starting downloads concurrently with {max_threads} threads...")

    with ThreadPoolExecutor(max_workers=max_threads) as executor:
        future_to_track = {
            executor.submit(download_song, track_name, artist_names, out_dir): (track_name, artist_names)
            for track_name, artist_names in tracks
        }

        for future in as_completed(future_to_track):
            success = future.result()
            if success:
                songs_processed += 1
            else:
                songs_failed += 1

    logger.info(
        f"Process completed. Songs successfully processed: {songs_processed}, Failed: {songs_failed}"
    )


if __name__ == "__main__":
    main()
