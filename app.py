from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
import subprocess, requests, os, uuid, shutil, json, time, textwrap, httpx

app = FastAPI()

# Configuration
TEMP_DIR = "/tmp/ffmpeg_jobs"
os.makedirs(TEMP_DIR, exist_ok=True)

# ARCHITECT: Verify this path! 
# Run 'ls /usr/share/fonts/truetype/noto/' to check.
FONT_PATH = "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf"

# =====================================================
# Utilities
# =====================================================

def ffmpeg(cmd: list):
    """Utility to run subprocess commands consistently."""
    subprocess.run(cmd, check=True)

def download_asset(url: str, path: str, label: str):
    """Standard downloader used across all endpoints."""
    if not url or str(url).lower() in ["none", "undefined", "null", ""]:
        return False
    try:
        r = requests.get(url, timeout=60, stream=True)
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as e:
        print(f"Download failed for {label}: {e}")
        return False

# =====================================================
# CINEMATIC RENDERER WITH AUTO-SUBTITLES
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
        bgm_url = payload.get("bgm_url")

        audio_local = f"{job_path}/voice.mp3"
        if not download_asset(audio_url, audio_local, "Audio"):
            raise Exception("Audio download failed.")

        duration_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", audio_local]
        duration = float(subprocess.run(duration_cmd, capture_output=True, text=True).stdout.strip())
        
        valid_images = []
        for i, url in enumerate(image_urls):
            path = f"{job_path}/img_{i}.png"
            if download_asset(url, path, f"Img_{i}"):
                valid_images.append(path)

        if not valid_images:
            raise Exception("No valid images found.")
        while len(valid_images) < 3:
            valid_images.append(valid_images[0])

        time_per_shot = duration / 3
        wrapped_sub = "\n".join(textwrap.wrap(subtitle_text, width=38))
        clean_sub = wrapped_sub.replace("'", "").replace('"', '').replace(":", "")

        clip_files = []
        for i in range(3):
            out = f"{job_path}/c_{i}.mp4"
            z = ["0.0005", "-0.0003", "0.0007"][i]
            fr = int(time_per_shot * 30)
            drawtext = f",drawtext=text='{clean_sub}':fontfile={FONT_PATH}:fontcolor=white:fontsize=40:box=1:boxcolor=black@0.5:boxborderw=20:line_spacing=15:x=(w-text_w)/2:y=h-160" if os.path.exists(FONT_PATH) else ""
            filters = f"scale=4000:-1,setsar=1/1,zoompan=z='1+{z}*on':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={fr}:s=1280x720:fps=30,scale=1280:720{drawtext},unsharp=3:3:1.5"
            ffmpeg(["ffmpeg", "-y", "-loop", "1", "-i", valid_images[i], "-filter_complex", filters, "-t", str(time_per_shot), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", out])
            clip_files.append(out)

        merged = f"{job_path}/merged.mp4"
        with open(f"{job_path}/list.txt", "w") as f:
            for c in clip_files: f.write(f"file '{os.path.abspath(c)}'\n")
        ffmpeg(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", f"{job_path}/list.txt", "-c", "copy", merged])

        final = f"{job_path}/final.mp4"
        if download_asset(bgm_url, f"{job_path}/bgm.mp3", "BGM"):
            ffmpeg(["ffmpeg", "-y", "-i", merged, "-i", audio_local, "-i", f"{job_path}/bgm.mp3", "-filter_complex", "[1:a]volume=1.3[v]; [2:a]volume=0.08[bg]; [v][bg]amix=inputs=2:duration=first", "-c:v", "copy", "-c:a", "aac", "-shortest", final])
        else:
            ffmpeg(["ffmpeg", "-y", "-i", merged, "-i", audio_local, "-c:v", "copy", "-c:a", "aac", "-shortest", final])

        return FileResponse(final, media_type="video/mp4")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# =====================================================
# FAST CONCAT ENDPOINT
# =====================================================

@app.post("/concat")
def concat_videos(payload: dict):
    job_id = str(uuid.uuid4())
    workdir = f"{TEMP_DIR}/{job_id}"
    os.makedirs(workdir, exist_ok=True)
    
    try:
        videos = payload.get("videos", [])
        output_name = payload.get("output_name", "final_story.mp4")
        local_files = []
        
        for i, url in enumerate(videos):
            path = f"{workdir}/s_{i}.mp4"
            if download_asset(url, path, f"Scene_{i}"):
                local_files.append(path)

        if not local_files:
            raise Exception("No valid scene videos found to join.")

        list_file = f"{workdir}/list.txt"
        with open(list_file, "w") as f:
            for p in local_files:
                f.write(f"file '{os.path.abspath(p)}'\n")

        output_path = f"{workdir}/{output_name}"
        ffmpeg(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", output_path])

        return FileResponse(output_path, media_type="video/mp4", filename=output_name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# =====================================================
# FINAL BRANDING ENDPOINT
# =====================================================

from fastapi import BackgroundTasks

# ... (keep your other imports and utilities)

@app.post("/apply_branding")
async def apply_branding(payload: dict, background_tasks: BackgroundTasks):
    """
    Starts the branding process and returns immediately to n8n.
    """
    video_url = payload.get("video_url")
    watermark_url = payload.get("watermark_url")
    resume_url = payload.get("resume_url") # The n8n Wait URL
    output_name = payload.get("output_name", "branded_final.mp4")

    if not video_url or not watermark_url:
        raise HTTPException(400, "Missing video_url or watermark_url")

    # This starts the heavy work in the background
    background_tasks.add_task(
        process_branding_and_callback, 
        video_url, watermark_img_url=watermark_url, 
        resume_url=resume_url, 
        output_name=output_name
    )

    return {"status": "accepted", "message": "Branding started. n8n will be notified."}

def process_branding_and_callback(video_url, watermark_img_url, resume_url, output_name):
    job_id = str(uuid.uuid4())
    workdir = f"{TEMP_DIR}/{job_id}"
    os.makedirs(workdir, exist_ok=True)
    
    input_video = f"{workdir}/input.mp4"
    watermark_img = f"{workdir}/watermark.png"
    output_video = f"{workdir}/{output_name}"

    try:
        # 1. Download assets using your existing download_asset function
        download_asset(video_url, input_video, "Main Video")
        download_asset(watermark_img_url, watermark_img, "Watermark")

        # 2. Run FFmpeg (High Speed)
        # Using ultrafast to prevent long server-side wait
        cmd = [
            "ffmpeg", "-y", "-i", input_video, "-i", watermark_img,
            "-filter_complex", "[1:v]scale=200:-1,format=rgba,colorchannelmixer=aa=0.4[wm];[0:v][wm]overlay=W-w-30:30",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "24", "-c:a", "copy", "-threads", "0",
            output_video
        ]
        subprocess.run(cmd, check=True)

        # 3. Callback to n8n (Wakes up the Wait Node)
        if resume_url:
            # We use requests here. Since this is in a background thread, 
            # it doesn't block the main API.
            requests.post(resume_url, json={
                "status": "success",
                "video_url": f"http://YOUR_SERVER_IP_OR_FQDN/download/{job_id}/{output_name}",
                "message": "Branding complete"
            })

    except Exception as e:
        print(f"Branding Task Failed: {e}")
        if resume_url:
            requests.post(resume_url, json={"status": "error", "error": str(e)})

# =====================================================
# SYSTEM ROUTES
# =====================================================

@app.get("/cleanup")
def cleanup_system(max_age_hours: int = 1):
    now = time.time()
    count = 0
    for folder in os.listdir(TEMP_DIR):
        path = os.path.join(TEMP_DIR, folder)
        if os.path.isdir(path) and os.stat(path).st_mtime < now - (max_age_hours * 3600):
            shutil.rmtree(path, ignore_errors=True)
            count += 1
    return {"status": "success", "cleared": count}

@app.get("/")
def read_root():
    return {"status": "Render API is online", "endpoints": ["/render_scene_v3_subtitles", "/concat", "/apply_branding", "/cleanup"]}