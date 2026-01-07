from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
import subprocess, requests, os, uuid, shutil, json, time, textwrap

app = FastAPI()

# Configuration
TEMP_DIR = "/tmp/ffmpeg_jobs"
os.makedirs(TEMP_DIR, exist_ok=True)
# ARCHITECT: Ensure this path is correct for your system!
FONT_PATH = "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf"

def download_asset(url: str, path: str, label: str):
    """Downloads and strictly validates the file. Returns True if valid, False if broken."""
    if not url or str(url).lower() in ["none", "undefined", "null", ""]:
        return False
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        # Check for 0KB or XML error pages
        if len(r.content) < 500:
            print(f"[Warning] {label} at {url} is empty or too small. Skipping.")
            return False
        with open(path, "wb") as f:
            f.write(r.content)
        return True
    except Exception as e:
        print(f"[Error] Failed to download {label}: {str(e)}")
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

        # 1. Download Required Audio
        audio_local = f"{job_path}/voice.mp3"
        if not download_asset(audio_url, audio_local, "Narration"):
            raise Exception(f"CRITICAL: Narration audio for Scene {scene} is missing or 0KB.")
        
        # Get duration
        dur_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", audio_local]
        duration = float(subprocess.run(dur_cmd, capture_output=True, text=True).stdout.strip())
        
        # 2. Download Images (Fault Tolerant)
        valid_images = []
        for i, url in enumerate(image_urls):
            path = f"{job_path}/img_{i}.png"
            if download_asset(url, path, f"Image {i}"):
                valid_images.append(path)

        if not valid_images:
            raise Exception(f"CRITICAL: Scene {scene} has ZERO valid images in R2 bucket.")

        # Fill the gaps if some images failed
        while len(valid_images) < 3:
            valid_images.append(valid_images[0])

        # 3. Create Video Clips
        time_per_shot = duration / 3
        wrapped_sub = "\n".join(textwrap.wrap(subtitle_text, width=38))
        clean_sub = wrapped_sub.replace("'", "").replace('"', '').replace(":", "")

        clip_files = []
        for i in range(3):
            out_clip = f"{job_path}/clip_{i}.mp4"
            z = ["0.0005", "-0.0003", "0.0007"][i]
            fr = int(time_per_shot * 30)

            drawtext = ""
            if os.path.exists(FONT_PATH):
                drawtext = f",drawtext=text='{clean_sub}':fontfile={FONT_PATH}:fontcolor=white:fontsize=40:box=1:boxcolor=black@0.5:boxborderw=20:line_spacing=15:x=(w-text_w)/2:y=h-160"

            # QUALITY SETTINGS: 4K Super-sampling + Anti-Shake
            filters = f"scale=4000:-1,setsar=1/1,zoompan=z='1+{z}*on':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={fr}:s=1280x720:fps=30,scale=1280:720{drawtext},unsharp=3:3:1.5"

            subprocess.run([
                "ffmpeg", "-y", "-loop", "1", "-i", valid_images[i],
                "-filter_complex", filters, "-t", str(time_per_shot),
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", out_clip
            ], check=True, capture_output=True)
            clip_files.append(out_clip)

        # 4. Final Merging
        merged_silent = f"{job_path}/merged_silent.mp4"
        list_txt = f"{job_path}/list.txt"
        with open(list_txt, "w") as f:
            for c in clip_files: f.write(f"file '{os.path.abspath(c)}'\n")
        
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_txt, "-c", "copy", merged_silent], check=True)

        final_video = f"{job_path}/final_output.mp4"
        
        # BGM logic with "None" safety
        is_bgm_valid = download_asset(bgm_url, f"{job_path}/bgm.mp3", "BGM")
        
        if is_bgm_valid:
            subprocess.run([
                "ffmpeg", "-y", "-i", merged_silent, "-i", audio_local, "-i", f"{job_path}/bgm.mp3",
                "-filter_complex", "[1:a]volume=1.3[v]; [2:a]volume=0.08[bg]; [v][bg]amix=inputs=2:duration=first",
                "-c:v", "copy", "-c:a", "aac", "-shortest", final_video
            ], check=True)
        else:
            subprocess.run(["ffmpeg", "-y", "-i", merged_silent, "-i", audio_local, "-c:v", "copy", "-c:a", "aac", "-shortest", final_video], check=True)

        # 5. FINAL CHECK: Does the file actually exist before we return it?
        if not os.path.exists(final_video):
            raise Exception("FFmpeg failed to generate the final file.")

        return FileResponse(final_video, media_type="video/mp4", filename=f"scene_{scene}.mp4")

    except Exception as e:
        # DO NOT delete the folder yet so you can inspect the logs
        print(f"CRITICAL ERROR: {str(e)}")
        # Send the exact error back to n8n
        raise HTTPException(status_code=500, detail=str(e))