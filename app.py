from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
import subprocess
import requests
import os
import uuid
import shutil
import json
import time

app = FastAPI()

# Configuration
TEMP_DIR = "/tmp/ffmpeg_jobs"
os.makedirs(TEMP_DIR, exist_ok=True)

# Path to your Hindi Font - Update this based on your OS
# Common paths: 
# Ubuntu: "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"
# Docker: "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

# =====================================================
# Utilities
# =====================================================

def run_ffmpeg(cmd: list):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result
    except subprocess.CalledProcessError as e:
        print(f"FFmpeg Error Output: {e.stderr}")
        raise Exception(f"FFmpeg failed: {e.stderr}")

def download_file(url: str, path: str):
    if not url or "undefined" in str(url).lower():
        raise ValueError(f"Invalid URL provided: {url}")
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    with open(path, "wb") as f:
        f.write(r.content)

def get_audio_duration(path: str) -> float:
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "json", path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(json.loads(result.stdout)["format"]["duration"])

# =====================================================
# ENDPOINTS
# =====================================================

@app.post("/render_scene_v3_subtitles")
async def render_scene_v3_subtitles(payload: dict):
    job_id = str(uuid.uuid4())
    job_path = f"{TEMP_DIR}/{job_id}"
    os.makedirs(job_path, exist_ok=True)
    
    try:
        scene = str(payload.get("scene", "1"))
        image_urls = payload.get("image_urls", [])
        audio_url = payload.get("audio_url")
        subtitle_text = payload.get("subtitle_text", "")
        bgm_url = payload.get("bgm_url", None) # Optional BGM

        # 1. Download Core Assets
        audio_local = f"{job_path}/audio.mp3"
        download_file(audio_url, audio_local)
        
        total_duration = get_audio_duration(audio_local)
        time_per_shot = total_duration / len(image_urls)
        
        # 2. Process each image into a cinematic clip
        clip_files = []
        for i, url in enumerate(image_urls):
            img_local = f"{job_path}/img_{i}.png"
            clip_output = f"{job_path}/clip_{i}.mp4"
            download_file(url, img_local)
            
            # Dynamic Zoom Logic
            zooms = ["0.0005", "-0.0003", "0.0008"] # Subtler, more professional
            z_val = zooms[i] if i < len(zooms) else "0.0004"
            frames = int(time_per_shot * 30)

            # Subtitle Filter (Safe Escaping)
            clean_sub = subtitle_text.replace("'", "").replace('"', '').replace(":", "")
            
            # Check font exists or skip subtitles
            drawtext = ""
            if os.path.exists(FONT_PATH):
                drawtext = (
                    f",drawtext=text='{clean_sub}':fontfile={FONT_PATH}:"
                    "fontcolor=white:fontsize=36:box=1:boxcolor=black@0.5:"
                    "boxborderw=10:x=(w-text_w)/2:y=h-100"
                )

            # FFmpeg Cinematic Pipeline
            # We scale to 2560 to save RAM but keep 2K crispness
            ffmpeg_cmd = [
                "ffmpeg", "-y", "-loop", "1", "-i", img_local,
                "-filter_complex", 
                (
                    f"scale=2560:-1,zoompan=z='1+{z_val}*on':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                    f"d={frames}:s=1280x720:fps=30{drawtext},unsharp=3:3:1.5"
                ),
                "-t", str(time_per_shot), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", clip_output
            ]
            run_ffmpeg(ffmpeg_cmd)
            clip_files.append(clip_output)

        # 3. Concat Clips
        list_txt = f"{job_path}/list.txt"
        with open(list_txt, "w") as f:
            for c in clip_files:
                f.write(f"file '{os.path.abspath(c)}'\n")
        
        merged_silent = f"{job_path}/merged_silent.mp4"
        run_ffmpeg(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_txt, "-c", "copy", merged_silent])

        # 4. Final Mix (Audio + Optional BGM)
        final_video = f"{job_path}/final_scene_{scene}.mp4"
        
        if bgm_url:
            bgm_local = f"{job_path}/bgm.mp3"
            download_file(bgm_url, bgm_local)
            # Mix Narration (Full) + BGM (Lowered)
            run_ffmpeg([
                "ffmpeg", "-y", "-i", merged_silent, "-i", audio_local, "-i", bgm_local,
                "-filter_complex", "[2:a]volume=0.1[bg];[1:a][bg]amix=inputs=2:duration=first",
                "-c:v", "copy", "-c:a", "aac", "-shortest", final_video
            ])
        else:
            run_ffmpeg([
                "ffmpeg", "-y", "-i", merged_silent, "-i", audio_local, 
                "-c:v", "copy", "-c:a", "aac", "-shortest", final_video
            ])

        return FileResponse(final_video, media_type="video/mp4", filename=f"scene_{scene}.mp4")

    except Exception as e:
        print(f"CRITICAL ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/concat")
def concat_videos(payload: dict):
    job_id = str(uuid.uuid4())
    workdir = f"{TEMP_DIR}/{job_id}"
    os.makedirs(workdir, exist_ok=True)
    
    try:
        videos = payload["videos"]
        output_name = payload.get("output_name", "final_story.mp4")
        local_files = []
        
        for i, url in enumerate(videos):
            path = f"{workdir}/s_{i}.mp4"
            download_file(url, path)
            local_files.append(path)

        list_file = f"{workdir}/list.txt"
        with open(list_file, "w") as f:
            for p in local_files: f.write(f"file '{os.path.abspath(p)}'\n")

        output_path = f"{workdir}/{output_name}"
        run_ffmpeg(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", output_path])

        return FileResponse(output_path, media_type="video/mp4")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# =====================================================
# CLEANUP SYSTEM
# =====================================================

@app.get("/cleanup")
def cleanup_old_jobs(max_age_hours: int = 2):
    """
    Deletes all temp folders older than X hours.
    Invoke this via a GET request from n8n at the end of your workflow.
    """
    now = time.time()
    deleted_count = 0
    for folder in os.listdir(TEMP_DIR):
        folder_path = os.path.join(TEMP_DIR, folder)
        if os.stat(folder_path).st_mtime < now - (max_age_hours * 3600):
            shutil.rmtree(folder_path, ignore_errors=True)
            deleted_count += 1
    return {"status": "success", "folders_cleared": deleted_count}