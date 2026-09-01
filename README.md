# Spotify Playlist to MP3

Convert your Spotify playlists to MP3 files by searching and downloading audio from YouTube.

## Features

- 🎵 Download songs from your Spotify playlist as MP3 files
- ⚡ Concurrent downloads with configurable thread count
- 🔀 Optional random shuffle of download order
- 📊 CSV-based playlist input
- 🎨 Colored logging output
- ⚙️ JSON configuration file for easy customization

## Requirements

- Python 3.7+
- FFmpeg (for audio conversion)
- yt-dlp (will be installed via pip)

### Install FFmpeg

**macOS:**
```bash
brew install ffmpeg
```

**Ubuntu/Debian:**
```bash
sudo apt-get install ffmpeg
```

**Windows:**
Download from [ffmpeg.org](https://ffmpeg.org/download.html) or:
```bash
choco install ffmpeg
```

## Installation

1. Clone or download this project
2. Create a virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Setup

### 1. Prepare Your Playlist

Export your Spotify playlist as CSV with the following columns:
- `Track Name` - Name of the song
- `Artist Name(s)` - Artist(s) performing the song

Example `data.csv`:
```csv
Track Name,Artist Name(s)
Bohemian Rhapsody,Queen
Imagine,John Lennon
Hotel California,Eagles
```

### 2. Configuration

Edit `options.json` to customize behavior:

```json
{
    "random_order": false,
    "threads": 6
}
```

- **random_order** (boolean): Shuffle tracks before downloading (default: false)
- **threads** (integer): Number of concurrent download threads (default: 6, max recommended: 10)

## Usage

Place your `data.csv` file in the project directory, then run:

```bash
python main.py
```

Downloaded MP3 files will be saved to the `out/` directory.

## Output

The script provides detailed logging:
- ✅ Successfully downloaded tracks
- ❌ Failed downloads with error reasons
- ℹ️ Process statistics (total processed, failed count)

Example output:
```
2026-09-01 23:20:01,144 - INFO - Starting Spotify Playlist to MP3 conversion process
2026-09-01 23:20:01,145 - INFO - Random order enabled. Shuffling tracks...
2026-09-01 23:20:01,146 - INFO - Starting downloads concurrently with 6 threads...
2026-09-01 23:20:06,865 - INFO - Successfully downloaded: 'Song Name Artist Name'
2026-09-01 23:20:13,989 - INFO - Process completed. Songs successfully processed: 5, Failed: 2
```

## Troubleshooting

### HTTP 403 Forbidden Errors
- These occur when YouTube blocks the request (geographic restrictions, age-restricted content, or video unavailability)
- Try updating yt-dlp: `pip install --upgrade yt-dlp`
- Some videos may simply be unavailable in your region

### No Files Downloaded
- Ensure `data.csv` exists in the project root
- Check the CSV format matches the expected columns
- Verify FFmpeg is installed and accessible

### Slow Downloads
- Reduce the `threads` value in `options.json` if system is overloaded
- Increase it (up to 10) for faster parallel downloads if your connection allows

## Project Structure

```
spotify-playlist-to-mp3/
├── main.py           # Main script
├── data.csv          # Your playlist (create this)
├── options.json      # Configuration (auto-created)
├── out/              # Downloaded MP3 files (auto-created)
└── README.md         # This file
```

## License

MIT License - Feel free to use and modify as needed.
