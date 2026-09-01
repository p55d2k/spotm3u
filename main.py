import csv
import logging
import os
import random
import json
import yt_dlp
from concurrent.futures import ThreadPoolExecutor, as_completed


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


def download_song(query, out_dir):
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(out_dir, "%(title)s.%(ext)s"),
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
            # ytsearch1: searches and returns the first result
            ydl.download([f"ytsearch1:{query}"])
            logger.info(f"Successfully downloaded: '{query}'")
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

    queries = []
    for row in rows:
        track_name = row.get("Track Name")
        artist_names = row.get("Artist Name(s)")
        # Replace ';' in artists with ', ' for a better search query
        artist_names_clean = artist_names.replace(";", ", ") if artist_names else ""
        queries.append(f"{track_name} {artist_names_clean}".strip())

    max_threads = max(1, config.get("threads", 6))
    logger.info(f"Starting downloads concurrently with {max_threads} threads...")

    # Using ThreadPoolExecutor for concurrent downloads
    with ThreadPoolExecutor(max_workers=max_threads) as executor:
        future_to_query = {
            executor.submit(download_song, q, out_dir): q for q in queries
        }

        for future in as_completed(future_to_query):
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
