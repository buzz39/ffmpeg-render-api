from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
import subprocess
import requests
import os
import uuid
import shutil
import json

app = FastAPI()

# =====================================================
# Utilities
# =====================================================

def ffmpeg(cmd: list, timeout=600):
    subprocess.run(cmd, check=True, timeout=timeout)

def download(url: str, path: str):
    if not url or "[undefined]" in url:
        raise HTTPException(400, "Invalid URL")
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    with open(path, "wb") as f:
        f.write(r.content)

def audio_duration(path: str) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            path
        ],
        capture_output=True,
        text=True,
        check=True
    )
    return float(json.loads(result.stdout)["format"]["duration"])

def download_video(url: str, output_path: str):
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)

# =====================================================
# LEGACY ENDPOINTS (UNCHANGED)
# =====================================================

@app.post("/render")
def render_scene(payload: dict):
    try:
        scene = str(payload["scene"])
        image_url = payload["image_url"]
        audio_url = payload["audio_url"]
    except KeyError:
        raise HTTPException(400, "Missing required fields")

    image_file = f"{scene}.png"
    audio_file = f"{scene}.mp3"
    output_file = f"{scene}.mp4"

    download(image_url, image_file)
    download(audio_url, audio_file)

    ffmpeg([
        "ffmpeg", "-y",
        "-loop", "1", "-i", image_file,
        "-i", audio_file,
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-pix_fmt", "yuv420p",
        "-shortest",
        output_file
    ])

    return FileResponse(output_file, media_type="video/mp4", filename=output_file)

@app.post("/concat")
def concat_videos(payload: dict, background_tasks: BackgroundTasks):
    videos = payload["videos"]
    output_name = payload.get("output_name", "final_story.mp4")

    job_id = str(uuid.uuid4())
    workdir = f"/tmp/{job_id}"
    os.makedirs(workdir, exist_ok=True)

    local_files = []
    try:
        for i, url in enumerate(videos):
            # Handle potential n8n "undefined" or null issues
            if not url or "http" not in str(url):
                continue
                
            path = f"{workdir}/scene_{i}.mp4"
            download_video(url, path)
            local_files.append(path)

        if not local_files:
            raise HTTPException(400, "No valid video URLs provided")

        list_file = f"{workdir}/list.txt"
        with open(list_file, "w") as f:
            for p in local_files:
                # Use absolute paths for ffmpeg concat
                f.write(f"file '{os.path.abspath(p)}'\n")

        output_path = f"{workdir}/{output_name}"

        # THE FIX: Use '-c copy' to skip re-encoding. 
        # This makes the process 100x faster.
        ffmpeg([
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", list_file,
            "-c", "copy",  # <--- This is the magic line
            output_path
        ])

        # We return the file, then clean up the directory
        # Note: BackgroundTasks should be handled carefully with FileResponse
        return FileResponse(output_path, media_type="video/mp4", filename=output_name)
    
    except Exception as e:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(500, f"Concat failed: {str(e)}")

@app.post("/render_hook")
def render_hook(payload: dict):
    image_url = payload["image_url"]
    text = payload["text"]

    output = "hook.mp4"

    ffmpeg([
        "ffmpeg", "-y",
        "-loop", "1", "-i", image_url,
        "-filter_complex",
        (
            "zoompan=z='1+0.002*n':"
            "x='iw/2-(iw/zoom/2)':"
            "y='ih/2-(ih/zoom/2)':"
            "d=75,"
            "scale=1280:720,"
            f"drawtext=text='{text}':"
            "fontcolor=white:fontsize=48:"
            "box=1:boxcolor=black@0.6:"
            "x=(w-text_w)/2:y=h/2"
        ),
        "-t", "3",
        output
    ])

    return FileResponse(output, media_type="video/mp4")

# =====================================================
# NEW: PROFESSIONAL CINEMATIC SCENE RENDERER
# =====================================================

@app.post("/render_scene_cinematic")
def render_scene_cinematic(payload: dict):
    try:
        scene = str(payload["scene"])
        # We now expect a LIST of 3 image URLs
        image_urls = payload["image_urls"] 
        audio_url = payload["audio_url"]
    except KeyError:
        raise HTTPException(400, "Missing required fields. Need image_urls (list)")

    job = f"/tmp/{uuid.uuid4()}"
    os.makedirs(job, exist_ok=True)
    audio_path = f"{job}/audio.mp3"
    download(audio_url, audio_path)
    
    total_duration = audio_duration(audio_path)
    # Split the time between the 3 images
    time_per_shot = total_duration / len(image_urls)
    
    clip_files = []
    for i, url in enumerate(image_urls):
        img_path = f"{job}/img_{i}.png"
        clip_path = f"{job}/clip_{i}.mp4"
        download(url, img_path)
        
        # Vary the zoom for each shot to keep it interesting
        # Shot 1: Slow zoom in, Shot 2: Slow zoom out, Shot 3: Fast zoom in
        zooms = ["0.0005", "-0.0005", "0.001"]
        z_val = zooms[i] if i < len(zooms) else "0.0007"
        
        ffmpeg([
            "ffmpeg", "-y", "-loop", "1", "-i", img_path,
            "-filter_complex", 
            f"zoompan=z='1+{z_val}*on':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={int(time_per_shot*30)}:s=1280x720,fps=30",
            "-t", str(time_per_shot), "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", clip_path
        ])
        clip_files.append(clip_path)

    # Concat the 3 shots together
    list_path = f"{job}/list.txt"
    with open(list_path, "w") as f:
        for c in clip_files: f.write(f"file '{c}'\n")
    
    merged_silent = f"{job}/merged.mp4"
    ffmpeg(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", merged_silent])

    # Add the audio back
    final_path = f"{job}/final.mp4"
    ffmpeg(["ffmpeg", "-y", "-i", merged_silent, "-i", audio_path, "-c:v", "copy", "-c:a", "aac", "-shortest", final_path])

    return FileResponse(final_path, media_type="video/mp4")
