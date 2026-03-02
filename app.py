from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
import subprocess, requests, os, uuid, shutil, json, time, textwrap, logging
from typing import Optional, List

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI()

# Configuration
TEMP_DIR = "/tmp/ffmpeg_jobs"
os.makedirs(TEMP_DIR, exist_ok=True)

# ARCHITECT: Verify this path! 
# Run 'ls /usr/share/fonts/truetype/noto/' to check.
FONT_PATH = "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf"

# Simple job tracking for async processing
job_status = {}

def download_asset(url: str, path: str, label: str) -> bool:
    if not url or str(url).lower() in ["none", "undefined", "null", ""]:
        logger.warning(f"{label}: Empty or invalid URL provided")
        return False
    try:
        logger.info(f"{label}: Downloading from {url[:100]}...")
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        if len(r.content) < 500:
            logger.error(f"{label}: Downloaded content too small ({len(r.content)} bytes)")
            return False
        with open(path, "wb") as f:
            f.write(r.content)
        logger.info(f"{label}: Successfully downloaded {len(r.content)} bytes to {path}")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"{label}: Download failed - {str(e)}")
        return False
    except Exception as e:
        logger.error(f"{label}: Unexpected error during download - {str(e)}")
        return False

@app.post("/render_scene_v3_subtitles")
async def render_scene_v3_subtitles(payload: dict, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    
    logger.info(f"Starting async render job {job_id}")
    
    # Input validation
    if not payload.get("audio_url"):
        raise HTTPException(status_code=400, detail="audio_url is required")
    
    if not payload.get("image_urls") or not isinstance(payload.get("image_urls"), list):
        raise HTTPException(status_code=400, detail="image_urls must be a non-empty list")
    
    # Initialize job status
    job_status[job_id] = {
        "status": "processing",
        "message": "Job started, downloading assets",
        "created_at": time.time()
    }
    
    # Start background processing
    background_tasks.add_task(process_render_job, job_id, payload)
    
    # Return immediately with job_id
    return JSONResponse({
        "job_id": job_id,
        "status": "processing",
        "message": "Render job started successfully",
        "check_status_url": f"/job_status/{job_id}",
        "download_url": f"/download/{job_id}"
    })

def process_render_job(job_id: str, payload: dict):
    """Background function to process the render job"""
    job_path = f"{TEMP_DIR}/{job_id}"
    os.makedirs(job_path, exist_ok=True)
    
    try:
        job_status[job_id] = {"status": "downloading", "message": "Downloading assets"}
        
        scene = str(payload.get("scene", "1"))
        image_urls = payload.get("image_urls", [])
        audio_url = payload.get("audio_url")
        subtitle_text = payload.get("subtitle_text", "")
        bgm_url = payload.get("bgm_url")
        
        logger.info(f"Job {job_id}: Processing scene {scene} with {len(image_urls)} images")

        # Download audio
        audio_local = f"{job_path}/voice.mp3"
        if not download_asset(audio_url, audio_local, "Audio"):
            job_status[job_id] = {"status": "failed", "message": "Failed to download audio file"}
            return

        # Get audio duration
        try:
            duration_result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", audio_local], 
                capture_output=True, text=True, check=True
            )
            duration = float(duration_result.stdout.strip())
            logger.info(f"Job {job_id}: Audio duration: {duration:.2f} seconds")
        except (subprocess.CalledProcessError, ValueError) as e:
            logger.error(f"Job {job_id}: Failed to get audio duration - {str(e)}")
            job_status[job_id] = {"status": "failed", "message": "Invalid audio file or ffprobe error"}
            return
        
        # Download and validate images
        job_status[job_id] = {"status": "downloading", "message": "Downloading images"}
        valid_images = []
        for i, url in enumerate(image_urls):
            path = f"{job_path}/img_{i}.png"
            if download_asset(url, path, f"Img_{i}"):
                valid_images.append(path)
        
        logger.info(f"Job {job_id}: Successfully downloaded {len(valid_images)} out of {len(image_urls)} images")

        if not valid_images:
            job_status[job_id] = {"status": "failed", "message": "No valid images could be downloaded"}
            return
        
        # Duplicate images if we have fewer than 3
        while len(valid_images) < 3:
            valid_images.append(valid_images[0])
            
        logger.info(f"Job {job_id}: Using {len(valid_images)} images for rendering")

        time_per_shot = duration / 3
        wrapped_sub = "\n".join(textwrap.wrap(subtitle_text, width=38))
        clean_sub = wrapped_sub.replace("'", "").replace('"', '').replace(":", "")
        
        # Check font availability
        font_available = os.path.exists(FONT_PATH)
        logger.info(f"Job {job_id}: Font available at {FONT_PATH}: {font_available}")
        if not font_available:
            logger.warning(f"Job {job_id}: Font not found, subtitles will be disabled")

        # Create individual clips
        job_status[job_id] = {"status": "rendering", "message": "Creating video clips"}
        clip_files = []
        for i in range(3):
            out = f"{job_path}/c_{i}.mp4"
            z = ["0.0005", "-0.0003", "0.0007"][i]
            fr = int(time_per_shot * 30)
            
            if font_available and clean_sub.strip():
                drawtext = f",drawtext=text='{clean_sub}':fontfile={FONT_PATH}:fontcolor=white:fontsize=40:box=1:boxcolor=black@0.5:boxborderw=20:line_spacing=15:x=(w-text_w)/2:y=h-160"
            else:
                drawtext = ""
                
            filters = f"scale=4000:-1,setsar=1/1,zoompan=z='1+{z}*on':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={fr}:s=1280x720:fps=30,scale=1280:720{drawtext},unsharp=3:3:1.5"
            
            logger.info(f"Job {job_id}: Rendering clip {i+1}/3 (duration: {time_per_shot:.2f}s)")
            try:
                subprocess.run([
                    "ffmpeg", "-y", "-loop", "1", "-i", valid_images[i], 
                    "-filter_complex", filters, "-t", str(time_per_shot), 
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", out
                ], check=True, capture_output=True, text=True)
                clip_files.append(out)
                logger.info(f"Job {job_id}: Clip {i+1}/3 completed")
            except subprocess.CalledProcessError as e:
                logger.error(f"Job {job_id}: FFmpeg failed for clip {i+1} - {str(e)}")
                logger.error(f"Job {job_id}: FFmpeg stderr: {e.stderr}")
                job_status[job_id] = {"status": "failed", "message": f"Video rendering failed for clip {i+1}"}
                return

        # Merge clips
        job_status[job_id] = {"status": "merging", "message": "Merging video clips"}
        merged = f"{job_path}/merged.mp4"
        logger.info(f"Job {job_id}: Merging {len(clip_files)} clips")
        
        with open(f"{job_path}/list.txt", "w") as f:
            for c in clip_files: 
                f.write(f"file '{os.path.abspath(c)}'\n")
        
        try:
            subprocess.run([
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", 
                "-i", f"{job_path}/list.txt", "-c", "copy", merged
            ], check=True, capture_output=True, text=True)
            logger.info(f"Job {job_id}: Clips merged successfully")
        except subprocess.CalledProcessError as e:
            logger.error(f"Job {job_id}: Failed to merge clips - {str(e)}")
            job_status[job_id] = {"status": "failed", "message": "Failed to merge video clips"}
            return

        # Add audio and create final output
        job_status[job_id] = {"status": "finalizing", "message": "Adding audio tracks"}
        final = f"{job_path}/final.mp4"
        logger.info(f"Job {job_id}: Adding audio tracks")
        
        try:
            if download_asset(bgm_url, f"{job_path}/bgm.mp3", "BGM"):
                logger.info(f"Job {job_id}: Adding background music")
                subprocess.run([
                    "ffmpeg", "-y", "-i", merged, "-i", audio_local, "-i", f"{job_path}/bgm.mp3", 
                    "-filter_complex", "[1:a]volume=1.3[v]; [2:a]volume=0.08[bg]; [v][bg]amix=inputs=2:duration=first", 
                    "-c:v", "copy", "-c:a", "aac", "-shortest", final
                ], check=True, capture_output=True, text=True)
            else:
                logger.info(f"Job {job_id}: Adding voice audio only")
                subprocess.run([
                    "ffmpeg", "-y", "-i", merged, "-i", audio_local, 
                    "-c:v", "copy", "-c:a", "aac", "-shortest", final
                ], check=True, capture_output=True, text=True)
            
            logger.info(f"Job {job_id}: Render completed successfully")
            job_status[job_id] = {
                "status": "completed", 
                "message": "Render completed successfully",
                "result_file": final
            }
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Job {job_id}: Failed to add audio - {str(e)}")
            job_status[job_id] = {"status": "failed", "message": "Failed to add audio to video"}
    except Exception as e:
        logger.error(f"Job {job_id}: Unexpected error - {str(e)}", exc_info=True)
        job_status[job_id] = {"status": "failed", "message": f"Internal error: {str(e)}"}
        logger.info(f"Job {job_id}: Cleaning up temporary files")

@app.post("/merge")
async def merge_video_audio(payload: dict, background_tasks: BackgroundTasks):
    video_url = payload.get("video_url")
    audio_url = payload.get("audio_url")

    if not video_url:
        raise HTTPException(status_code=400, detail="video_url is required")
    if not audio_url:
        raise HTTPException(status_code=400, detail="audio_url is required")

    if not str(video_url).startswith(("http://", "https://")) or not str(audio_url).startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Invalid URL provided")

    job_id = str(uuid.uuid4())
    job_status[job_id] = {
        "status": "processing",
        "message": "Downloading assets",
        "created_at": time.time()
    }

    background_tasks.add_task(process_merge_job, job_id, video_url, audio_url)

    return JSONResponse({
        "job_id": job_id,
        "status": "processing",
        "message": "Merge job started",
        "check_status_url": f"/job_status/{job_id}",
        "download_url": f"/download/{job_id}"
    }, status_code=202)


def process_merge_job(job_id: str, video_url: str, audio_url: str):
    job_path = f"{TEMP_DIR}/{job_id}"
    os.makedirs(job_path, exist_ok=True)

    try:
        video_local = f"{job_path}/video.mp4"
        audio_local = f"{job_path}/audio.mp4"

        if not download_asset(video_url, video_local, "Video"):
            job_status[job_id] = {"status": "failed", "message": "Failed to download video"}
            return
        if not download_asset(audio_url, audio_local, "Audio"):
            job_status[job_id] = {"status": "failed", "message": "Failed to download audio"}
            return

        job_status[job_id] = {"status": "merging", "message": "Merging video and audio"}
        final = f"{job_path}/final.mp4"

        subprocess.run([
            "ffmpeg", "-y",
            "-i", video_local,
            "-i", audio_local,
            "-c:v", "copy", "-c:a", "aac", "-shortest",
            final
        ], check=True, capture_output=True, text=True)

        logger.info(f"Merge job {job_id}: Completed successfully")
        job_status[job_id] = {
            "status": "completed",
            "message": "Merge completed successfully",
            "result_file": final
        }

    except subprocess.CalledProcessError as e:
        logger.error(f"Merge job {job_id}: ffmpeg failed - {e.stderr}")
        job_status[job_id] = {"status": "failed", "message": f"ffmpeg error: {e.stderr[:200]}"}
    except Exception as e:
        logger.error(f"Merge job {job_id}: Unexpected error - {str(e)}", exc_info=True)
        job_status[job_id] = {"status": "failed", "message": f"Internal error: {str(e)}"}


@app.post("/concat")
def concat_videos(payload: dict):
    job_id = str(uuid.uuid4())
    workdir = f"{TEMP_DIR}/{job_id}"
    os.makedirs(workdir, exist_ok=True)
    
    logger.info(f"Concat job {job_id}: Starting with {len(payload.get('videos', []))} videos")
    
    try:
        videos = payload.get("videos", [])
        output_name = payload.get("output_name", "final_story.mp4")
        local_files = []
        
        # 1. Download only VALID URLs
        for i, url in enumerate(videos):
            if not url or "http" not in str(url):
                logger.warning(f"Concat job {job_id}: Skipping empty video URL at index {i}")
                continue
                
            path = f"{workdir}/s_{i}.mp4"
            try:
                logger.info(f"Concat job {job_id}: Downloading video {i+1}/{len(videos)}")
                r = requests.get(url, timeout=60)
                if r.status_code == 200 and len(r.content) > 1000:
                    with open(path, "wb") as f:
                        f.write(r.content)
                    local_files.append(path)
                    logger.info(f"Concat job {job_id}: Successfully downloaded video {i+1} ({len(r.content)} bytes)")
                else:
                    logger.warning(f"Concat job {job_id}: Invalid video at index {i} - status: {r.status_code}, size: {len(r.content)}")
            except Exception as e:
                logger.error(f"Concat job {job_id}: Failed to download video {i+1} - {str(e)}")

        if not local_files:
            logger.error(f"Concat job {job_id}: No valid scene videos found to join")
            raise HTTPException(status_code=400, detail="No valid scene videos found to join.")
        
        logger.info(f"Concat job {job_id}: Concatenating {len(local_files)} valid videos")

        # 2. Create the FFmpeg instructions file
        list_file = f"{workdir}/list.txt"
        with open(list_file, "w") as f:
            for p in local_files:
                f.write(f"file '{os.path.abspath(p)}'\n")

        # 3. Stitch them together (No re-encoding = Instant)
        output_path = f"{workdir}/{output_name}"
        try:
            logger.info(f"Concat job {job_id}: Running ffmpeg concat")
            subprocess.run([
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", 
                "-i", list_file, "-c", "copy", output_path
            ], check=True, capture_output=True, text=True)
            logger.info(f"Concat job {job_id}: Successfully created {output_name}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Concat job {job_id}: FFmpeg concat failed - {str(e)}")
            raise HTTPException(status_code=500, detail="Failed to concatenate videos")

        return FileResponse(output_path, media_type="video/mp4", filename=output_name)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Concat job {job_id}: Unexpected error - {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Concat failed: {str(e)}")

# =====================================================
# THE MISSING CLEANUP ROUTE
# =====================================================
@app.get("/cleanup")
def cleanup_system(max_age_hours: int = 1):
    logger.info(f"Starting cleanup of files older than {max_age_hours} hours")
    now = time.time()
    count = 0
    try:
        for folder in os.listdir(TEMP_DIR):
            path = os.path.join(TEMP_DIR, folder)
            if os.path.isdir(path) and os.stat(path).st_mtime < now - (max_age_hours * 3600):
                shutil.rmtree(path, ignore_errors=True)
                count += 1
        logger.info(f"Cleanup completed: removed {count} old job folders")
        return {"status": "success", "cleared": count}
    except Exception as e:
        logger.error(f"Cleanup failed: {str(e)}")
        return {"status": "error", "error": str(e), "cleared": count}

@app.get("/job_status/{job_id}")
def get_job_status(job_id: str):
    """Check job status using both memory tracking and file system"""
    # Check memory status first
    if job_id in job_status:
        status_info = job_status[job_id].copy()
        status_info["job_id"] = job_id
        
        # If marked as completed, verify file exists
        if status_info["status"] == "completed":
            final_path = os.path.join(TEMP_DIR, job_id, "final.mp4")
            if os.path.exists(final_path):
                status_info["file_size_bytes"] = os.path.getsize(final_path)
                status_info["download_url"] = f"/download/{job_id}"
            else:
                status_info["status"] = "processing"
                status_info["message"] = "Finalizing render"
        
        return status_info
    
    # Fallback to file system check
    job_path = os.path.join(TEMP_DIR, job_id)
    final_path = os.path.join(job_path, "final.mp4")
    
    if not os.path.exists(job_path):
        return {
            "job_id": job_id,
            "status": "not_found",
            "message": "Job not found"
        }
    
    if os.path.exists(final_path):
        file_size = os.path.getsize(final_path)
        return {
            "job_id": job_id,
            "status": "completed",
            "message": "Render completed successfully",
            "file_size_bytes": file_size,
            "download_url": f"/download/{job_id}"
        }
    else:
        return {
            "job_id": job_id,
            "status": "processing",
            "message": "Render in progress"
        }

@app.get("/download/{job_id}")
def download_job_result(job_id: str):
    """Download the completed render result"""
    final_path = os.path.join(TEMP_DIR, job_id, "final.mp4")
    
    if not os.path.exists(final_path):
        raise HTTPException(status_code=404, detail="Render not completed or file not found")
    
    return FileResponse(
        final_path, 
        media_type="video/mp4", 
        filename=f"scene_{job_id}.mp4"
    )

# Health check and debug endpoints
@app.get("/")
def read_root():
    return {"status": "Render API is Online"}

@app.get("/health")
def health_check():
    """Detailed health check with system information"""
    try:
        # Check ffmpeg availability
        ffmpeg_result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=10)
        ffmpeg_available = ffmpeg_result.returncode == 0
        
        # Check ffprobe availability  
        ffprobe_result = subprocess.run(["ffprobe", "-version"], capture_output=True, text=True, timeout=10)
        ffprobe_available = ffprobe_result.returncode == 0
        
        # Check font availability
        font_available = os.path.exists(FONT_PATH)
        
        # Check temp directory
        temp_writable = os.access(TEMP_DIR, os.W_OK)
        
        # Count active jobs
        active_jobs = len([d for d in os.listdir(TEMP_DIR) if os.path.isdir(os.path.join(TEMP_DIR, d))])
        
        health_status = {
            "status": "healthy" if all([ffmpeg_available, ffprobe_available, temp_writable]) else "unhealthy",
            "ffmpeg_available": ffmpeg_available,
            "ffprobe_available": ffprobe_available,
            "font_available": font_available,
            "font_path": FONT_PATH,
            "temp_directory_writable": temp_writable,
            "temp_directory": TEMP_DIR,
            "active_jobs": active_jobs
        }
        
        logger.info(f"Health check completed: {health_status['status']}")
        return health_status
        
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        return {
            "status": "unhealthy",
            "error": str(e)
        }

@app.get("/debug/system")
def debug_system():
    """Debug endpoint to check system configuration"""
    try:
        debug_info = {
            "temp_dir": TEMP_DIR,
            "temp_dir_exists": os.path.exists(TEMP_DIR),
            "temp_dir_writable": os.access(TEMP_DIR, os.W_OK),
            "font_path": FONT_PATH,
            "font_exists": os.path.exists(FONT_PATH)
        }
        
        # List available fonts
        font_dirs = ["/usr/share/fonts", "/usr/local/share/fonts", "/System/Library/Fonts"]
        available_fonts = []
        for font_dir in font_dirs:
            if os.path.exists(font_dir):
                try:
                    for root, dirs, files in os.walk(font_dir):
                        for file in files[:10]:  # Limit to first 10 fonts per directory
                            if file.endswith(('.ttf', '.otf')):
                                available_fonts.append(os.path.join(root, file))
                except Exception:
                    pass
        
        debug_info["available_fonts_sample"] = available_fonts[:20]  # First 20 fonts
        
        # Check ffmpeg installation
        try:
            ffmpeg_result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
            debug_info["ffmpeg_version"] = ffmpeg_result.stdout.split('\n')[0] if ffmpeg_result.returncode == 0 else "Not available"
        except Exception as e:
            debug_info["ffmpeg_version"] = f"Error: {str(e)}"
            
        return debug_info
        
    except Exception as e:
        return {"error": str(e)}

# Startup configuration - important for Coolify deployment
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 3000))  # Use Coolify's PORT env var
    uvicorn.run("app:app", host="0.0.0.0", port=port, log_level="info")