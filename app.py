from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
import subprocess
import requests
import os
import uuid
import shutil
import json
import time
import textwrap

app = FastAPI()

# Configuration
TEMP_DIR = "/tmp/ffmpeg_jobs"
os.makedirs(TEMP_DIR, exist_ok=True)
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf" # Ensure this exists!

def run_ffmpeg(cmd: list):
    try:
        # Use a timeout to prevent hanging processes
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=300)
        return result
    except subprocess.CalledProcessError as e:
        print(f"FFmpeg Error: {e.stderr}")
        raise Exception(f"FFmpeg failed: {e.stderr}")

def download_file(url: str, path: str, retries=5):
    """Downloads a file and verifies it is not a Cloudflare error page."""
    if not url or str(url).lower() in ["none", "undefined", "null", ""]:
        raise ValueError(f"Invalid URL provided: {url}")
    
    for i in range(retries):
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            
            # Check if the response is actually an image/audio or an XML error
            content_type = r.headers.get('Content-Type', '').lower()
            if "xml" in content_type or "text" in content_type:
                print(f"R2 Sync Delay: {url} returned XML. Retrying in 3s... (Attempt {i+1})")
                time.sleep(3)
                continue
                
            with open(path, "wb") as f:
                f.write(r.content)
            return # Success
            
        except Exception as e:
            if i == retries - 1:
                raise HTTPException(status_code=500, detail=f"Failed to download {url} after {retries} attempts: {str(e)}")
            time.sleep(3)

def get_audio_duration(path: str) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(json.loads(result.stdout)["format"]["duration"])

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
        bgm_url = payload.get("bgm_url")

        # 1. Download Narration
        audio_local = f"{job_path}/audio.mp3"
        download_file(audio_url, audio_local)
        total_duration = get_audio_duration(audio_local)
        time_per_shot = total_duration / len(image_urls)
        
        # 2. Wrap Hindi Subtitles for multi-line display
        wrapped_sub = "\n".join(textwrap.wrap(subtitle_text, width=45))
        # Escape characters that break FFmpeg
        clean_sub = wrapped_sub.replace("'", "").replace('"', '').replace(":", "")

        clip_files = []
        for i, url in enumerate(image_urls):
            img_local = f"{job_path}/img_{i}.png"
            clip_output = f"{job_path}/clip_{i}.mp4"
            download_file(url, img_local)
            
            # Very slow, smooth zoom values
            zooms = ["0.0004", "-0.0002", "0.0006"]
            z_val = zooms[i % 3]
            frames = int(time_per_shot * 30)

            # Drawtext logic
            drawtext = ""
            if os.path.exists(FONT_PATH):
                drawtext = (
                    f",drawtext=text='{clean_sub}':fontfile={FONT_PATH}:"
                    "fontcolor=white:fontsize=32:box=1:boxcolor=black@0.5:"
                    "boxborderw=15:line_spacing=10:x=(w-text_w)/2:y=h-140"
                )

            # ANTI-SHAKE PIPELINE:
            # 1. Scale to 4000px (Super-sampling)
            # 2. zoompan (The math is smoother at high res)
            # 3. Scale down to 1280x720 (Downsampling kills the jitter)
            filter_complex = (
                f"scale=4000:-1,setsar=1/1,"
                f"zoompan=z='1+{z_val}*on':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1280x720:fps=30,"
                f"scale=1280:720{drawtext},unsharp=3:3:1.5"
            )

            ffmpeg_cmd = [
                "ffmpeg", "-y", "-loop", "1", "-i", img_local,
                "-filter_complex", filter_complex,
                "-t", str(time_per_shot), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", clip_output
            ]
            run_ffmpeg(ffmpeg_cmd)
            clip_files.append(clip_output)

        # 3. Concat and Audio Mix
        list_txt = f"{job_path}/list.txt"
        with open(list_txt, "w") as f:
            for c in clip_files: f.write(f"file '{os.path.abspath(c)}'\n")
        
        merged_silent = f"{job_path}/merged.mp4"
        run_ffmpeg(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_txt, "-c", "copy", merged_silent])

        final_video = f"{job_path}/final_scene_{scene}.mp4"
        if bgm_url and str(bgm_url).lower() != "none":
            bgm_local = f"{job_path}/bgm.mp3"
            download_file(bgm_url, bgm_local)
                
            # MIXING LOGIC:
            # [1:a] is the Narration (Volume 1.0)
            # [2:a] is the BGM (Volume 0.12 - Very quiet)
            run_ffmpeg([
                "ffmpeg", "-y", 
                "-i", merged_silent, 
                "-i", audio_local, 
                "-i", bgm_local,
                "-filter_complex", "[1:a]volume=1.2[v]; [2:a]volume=0.10[bg]; [v][bg]amix=inputs=2:duration=first",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", final_video
            ])
        else:
            run_ffmpeg(["ffmpeg", "-y", "-i", merged_silent, "-i", audio_local, "-c:v", "copy", "-c:a", "aac", "-shortest", final_video])

        return FileResponse(final_video, media_type="video/mp4", filename=f"scene_{scene}.mp4")

    except Exception as e:
        shutil.rmtree(job_path, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/concat")
def concat_videos(payload: dict):
    job_id = str(uuid.uuid4())
    workdir = f"{TEMP_DIR}/{job_id}"
    os.makedirs(workdir, exist_ok=True)
    try:
        videos = payload["videos"]
        local_files = []
        for i, url in enumerate(videos):
            path = f"{workdir}/s_{i}.mp4"
            download_file(url, path)
            local_files.append(path)
        list_file = f"{workdir}/list.txt"
        with open(list_file, "w") as f:
            for p in local_files: f.write(f"file '{os.path.abspath(p)}'\n")
        output_path = f"{workdir}/final_story.mp4"
        run_ffmpeg(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", output_path])
        return FileResponse(output_path, media_type="video/mp4")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/cleanup")
def cleanup_old_jobs(max_age_hours: int = 1):
    now = time.time()
    for folder in os.listdir(TEMP_DIR):
        path = os.path.join(TEMP_DIR, folder)
        if os.stat(path).st_mtime < now - (max_age_hours * 3600):
            shutil.rmtree(path, ignore_errors=True)
    return {"status": "success"}