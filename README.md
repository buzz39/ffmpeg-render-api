# FFmpeg Render API

A high-performance, asynchronous video rendering service built with **FastAPI** and **FFmpeg**. It generates MP4 videos from images and audio with subtitle overlays, zoom/pan effects, and optional background music — all through a simple REST API.

## Features

- **Asynchronous Rendering** — Submit jobs and poll for completion; no request timeouts on long renders
- **Image-to-Video** — Converts still images into cinematic clips with smooth zoom and pan effects
- **Subtitle Overlay** — Burns styled subtitles directly into the video with automatic text wrapping
- **Background Music Mixing** — Blends voice audio with background music at configurable volumes
- **Video Concatenation** — Stitches multiple MP4 files together without re-encoding for instant results
- **Job Management** — Track job status, download results, and clean up old files
- **Health Monitoring** — Built-in health check and system debug endpoints
- **Docker Ready** — Ships with a production-ready Dockerfile

## Tech Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.10 |
| Framework | FastAPI |
| Server | Uvicorn (ASGI) |
| Video Processing | FFmpeg / FFprobe |
| HTTP Client | Requests |
| Containerization | Docker |

## Getting Started

### Prerequisites

- Python 3.10+
- FFmpeg and FFprobe installed and available on `PATH`
- *(Optional)* Docker

### Local Installation

```bash
# Install Python dependencies
pip install fastapi uvicorn requests

# Install FFmpeg
# Ubuntu / Debian
sudo apt-get update && sudo apt-get install -y ffmpeg

# macOS
brew install ffmpeg
```

### Running the Server

```bash
# Start on the default port (3000)
uvicorn app:app --host 0.0.0.0 --port 3000

# Or use the PORT environment variable
PORT=8080 python app.py
```

### Docker

```bash
# Build the image
docker build -t ffmpeg-render-api .

# Run the container
docker run -p 3000:3000 ffmpeg-render-api

# Run with a custom port
docker run -e PORT=8080 -p 8080:8080 ffmpeg-render-api
```

## API Reference

### Health & Status

#### `GET /`

Returns a simple status message confirming the API is online.

**Response:**
```json
{ "status": "Render API is Online" }
```

#### `GET /health`

Detailed health check — verifies FFmpeg, FFprobe, font availability, and temp directory access.

**Response:**
```json
{
  "status": "healthy",
  "ffmpeg_available": true,
  "ffprobe_available": true,
  "font_available": true,
  "temp_directory_writable": true,
  "active_jobs": 2
}
```

#### `GET /debug/system`

Returns system configuration details including FFmpeg version, available fonts, and directory status.

---

### Video Rendering

#### `POST /render_scene_v3_subtitles`

Starts an asynchronous video render job. Images are turned into cinematic clips with zoom/pan effects, subtitles are overlaid, and audio tracks are mixed together.

**Request Body:**
```json
{
  "audio_url": "https://example.com/voice.mp3",
  "image_urls": [
    "https://example.com/image1.png",
    "https://example.com/image2.png",
    "https://example.com/image3.png"
  ],
  "subtitle_text": "Your subtitle text here",
  "bgm_url": "https://example.com/background.mp3",
  "scene": "1"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `audio_url` | string | **Yes** | URL of the voice/narration audio file |
| `image_urls` | string[] | **Yes** | List of image URLs (minimum 1; padded to 3 automatically) |
| `subtitle_text` | string | No | Text to overlay as subtitles |
| `bgm_url` | string | No | URL of background music to mix in |
| `scene` | string | No | Scene identifier for logging purposes |

**Response:**
```json
{
  "job_id": "a1b2c3d4-...",
  "status": "processing",
  "message": "Render job started successfully",
  "check_status_url": "/job_status/a1b2c3d4-...",
  "download_url": "/download/a1b2c3d4-..."
}
```

---

### Video Concatenation

#### `POST /concat`

Concatenates multiple MP4 videos into a single file. Uses FFmpeg stream copy (no re-encoding) for fast processing.

**Request Body:**
```json
{
  "videos": [
    "https://example.com/scene1.mp4",
    "https://example.com/scene2.mp4"
  ],
  "output_name": "final_story.mp4"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `videos` | string[] | **Yes** | List of video URLs to concatenate |
| `output_name` | string | No | Output filename (default: `final_story.mp4`) |

**Response:** The concatenated video file is returned directly as a download.

---

### Job Management

#### `GET /job_status/{job_id}`

Check the status of an asynchronous render job.

**Response (in progress):**
```json
{
  "job_id": "a1b2c3d4-...",
  "status": "rendering",
  "message": "Creating video clips"
}
```

**Response (completed):**
```json
{
  "job_id": "a1b2c3d4-...",
  "status": "completed",
  "message": "Render completed successfully",
  "file_size_bytes": 5242880,
  "download_url": "/download/a1b2c3d4-..."
}
```

**Possible status values:** `processing`, `downloading`, `rendering`, `merging`, `finalizing`, `completed`, `failed`, `not_found`

#### `GET /download/{job_id}`

Download the completed video for a given job.

**Response:** MP4 file download.

#### `GET /cleanup?max_age_hours=1`

Removes temporary job files older than the specified age.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `max_age_hours` | integer | `1` | Delete jobs older than this many hours |

**Response:**
```json
{ "status": "success", "cleared": 5 }
```

## Render Pipeline

The `/render_scene_v3_subtitles` endpoint processes video through these stages:

1. **Download** — Fetches audio, images, and optional background music from provided URLs
2. **Validate** — Ensures assets meet minimum size requirements (500 bytes for audio/images)
3. **Clip Generation** — Creates 3 video clips from images with varying zoom/pan effects (1280×720, H.264, CRF 18, 30 fps)
4. **Subtitle Burn-in** — Overlays styled white text with a semi-transparent black background box
5. **Merge** — Concatenates clips using FFmpeg stream copy
6. **Audio Mix** — Combines voice audio (volume 1.3×) with optional background music (volume 0.08×)
7. **Output** — Saves the final MP4 for download

## Configuration

| Setting | Value | Notes |
|---------|-------|-------|
| Output Resolution | 1280×720 | Hardcoded |
| Video Codec | H.264 (`libx264`) | CRF 18 quality |
| Frame Rate | 30 fps | |
| Audio Codec | AAC | |
| Temp Directory | `/tmp/ffmpeg_jobs/` | Auto-created on startup |
| Font | NotoSansDevanagari-Bold | Falls back gracefully if missing |

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `3000` | Server listen port |

## License

This project is provided as-is. See the repository for license details.
