from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
import subprocess, requests, os, uuid, shutil, json, time, textwrap

app = FastAPI()

# Configuration
TEMP_DIR = "/tmp/ffmpeg_jobs"
os.makedirs(TEMP_DIR, exist_ok=True)

# ARCHITECT: Verify this path! 
# Run 'ls /usr/share/fonts/truetype/noto/' to check.
FONT_PATH = "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf"

def download_asset(url: str, path: str, label: str):
    if not url or str(url).lower() in ["none", "undefined", "null", ""]:
        return False
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        if len(r.content) < 500:
            return False
        with open(path, "wb") as f:
            f.write(r.content)
        return True
    except:
        return False

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

        duration = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", audio_local], capture_output=True, text=True).stdout.strip())
        
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
            subprocess.run(["ffmpeg", "-y", "-loop", "1", "-i", valid_images[i], "-filter_complex", filters, "-t", str(time_per_shot), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", out], check=True)
            clip_files.append(out)

        merged = f"{job_path}/merged.mp4"
        with open(f"{job_path}/list.txt", "w") as f:
            for c in clip_files: f.write(f"file '{os.path.abspath(c)}'\n")
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", f"{job_path}/list.txt", "-c", "copy", merged], check=True)

        final = f"{job_path}/final.mp4"
        if download_asset(bgm_url, f"{job_path}/bgm.mp3", "BGM"):
            subprocess.run(["ffmpeg", "-y", "-i", merged, "-i", audio_local, "-i", f"{job_path}/bgm.mp3", "-filter_complex", "[1:a]volume=1.3[v]; [2:a]volume=0.08[bg]; [v][bg]amix=inputs=2:duration=first", "-c:v", "copy", "-c:a", "aac", "-shortest", final], check=True)
        else:
            subprocess.run(["ffmpeg", "-y", "-i", merged, "-i", audio_local, "-c:v", "copy", "-c:a", "aac", "-shortest", final], check=True)

        return FileResponse(final, media_type="video/mp4")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/concat")
def concat_videos(payload: dict):
    job_id = str(uuid.uuid4())
    workdir = f"{TEMP_DIR}/{job_id}"
    os.makedirs(workdir, exist_ok=True)
    
    try:
        videos = payload.get("videos", [])
        output_name = payload.get("output_name", "final_story.mp4")
        local_files = []
        
        # 1. Download only VALID URLs
        for i, url in enumerate(videos):
            if not url or "http" not in str(url):
                print(f"Skipping empty video URL at index {i}")
                continue
                
            path = f"{workdir}/s_{i}.mp4"
            # Use a simple download here since these are internal rendered files
            r = requests.get(url, timeout=60)
            if r.status_code == 200 and len(r.content) > 1000: # Ensure it's not an empty file
                with open(path, "wb") as f:
                    f.write(r.content)
                local_files.append(path)

        if not local_files:
            raise Exception("No valid scene videos found to join.")

        # 2. Create the FFmpeg instructions file
        list_file = f"{workdir}/list.txt"
        with open(list_file, "w") as f:
            for p in local_files:
                f.write(f"file '{os.path.abspath(p)}'\n")

        # 3. Stitch them together (No re-encoding = Instant)
        output_path = f"{workdir}/{output_name}"
        # '-c copy' is critical here. It makes it 100x faster and maintains 100% quality.
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", output_path], check=True)

        return FileResponse(output_path, media_type="video/mp4", filename=output_name)

    except Exception as e:
        print(f"CONCAT FAILED: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# =====================================================
# THE MISSING CLEANUP ROUTE
# =====================================================
@app.get("/cleanup")
def cleanup_system(max_age_hours: int = 1):
    now = time.time()
    count = 0
    for folder in os.listdir(TEMP_DIR):
        path = os.path.join(TEMP_DIR, folder)
        if os.stat(path).st_mtime < now - (max_age_hours * 3600):
            shutil.rmtree(path, ignore_errors=True)
            count += 1
    return {"status": "success", "cleared": count}

# Root route for health check
@app.get("/")
def read_root():
    return {"status": "Render API is Online"}