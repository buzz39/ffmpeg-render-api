from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
import subprocess, requests, os, uuid, shutil, json, time, textwrap, logging, threading
from typing import Optional, List, Dict
from enum import Enum
from dataclasses import dataclass, asdict
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class JobStatus(Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    RENDERING = "rendering"
    MERGING = "merging"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass
class JobInfo:
    job_id: str
    status: JobStatus
    progress: int  # 0-100
    message: str
    created_at: datetime
    updated_at: datetime
    result_file: Optional[str] = None
    error: Optional[str] = None
    estimated_completion: Optional[datetime] = None

# Global job tracking
jobs: Dict[str, JobInfo] = {}
jobs_lock = threading.Lock()

app = FastAPI()

# Configuration
TEMP_DIR = "/tmp/ffmpeg_jobs"
os.makedirs(TEMP_DIR, exist_ok=True)

# ARCHITECT: Verify this path! 
# Run 'ls /usr/share/fonts/truetype/noto/' to check.
FONT_PATH = "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf"

def update_job_status(job_id: str, status: JobStatus, progress: int = 0, message: str = "", error: str = None, result_file: str = None):
    """Thread-safe job status update"""
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].status = status
            jobs[job_id].progress = progress
            jobs[job_id].message = message
            jobs[job_id].updated_at = datetime.now()
            if error:
                jobs[job_id].error = error
            if result_file:
                jobs[job_id].result_file = result_file
            logger.info(f"Job {job_id}: {status.value} - {progress}% - {message}")

def get_job_status(job_id: str) -> Optional[JobInfo]:
    """Get current job status"""
    with jobs_lock:
        return jobs.get(job_id)

def create_job(job_id: str) -> JobInfo:
    """Create new job entry"""
    job_info = JobInfo(
        job_id=job_id,
        status=JobStatus.PENDING,
        progress=0,
        message="Job created",
        created_at=datetime.now(),
        updated_at=datetime.now()
    )
    with jobs_lock:
        jobs[job_id] = job_info
    return job_info

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
    """Start async video rendering job and return immediately with job ID"""
    job_id = str(uuid.uuid4())
    create_job(job_id)
    
    # Input validation
    if not payload.get("audio_url"):
        update_job_status(job_id, JobStatus.FAILED, 0, "Missing audio_url", "audio_url is required")
        raise HTTPException(status_code=400, detail="audio_url is required")
    
    if not payload.get("image_urls") or not isinstance(payload.get("image_urls"), list):
        update_job_status(job_id, JobStatus.FAILED, 0, "Missing image_urls", "image_urls must be a non-empty list")
        raise HTTPException(status_code=400, detail="image_urls must be a non-empty list")
    
    # Start background processing
    background_tasks.add_task(process_render_job, job_id, payload)
    
    return JSONResponse({
        "job_id": job_id,
        "status": "pending",
        "message": "Render job started",
        "status_url": f"/job_status/{job_id}",
        "download_url": f"/download/{job_id}"
    })

def process_render_job(job_id: str, payload: dict):
    """Background task for processing video render job"""
    job_path = f"{TEMP_DIR}/{job_id}"
    os.makedirs(job_path, exist_ok=True)
    
    try:
        update_job_status(job_id, JobStatus.DOWNLOADING, 5, "Starting downloads")
        
        scene = str(payload.get("scene", "1"))
        image_urls = payload.get("image_urls", [])
        audio_url = payload.get("audio_url")
        subtitle_text = payload.get("subtitle_text", "")
        bgm_url = payload.get("bgm_url")
        
        logger.info(f"Job {job_id}: Processing scene {scene} with {len(image_urls)} images")
    
        # Download audio
        update_job_status(job_id, JobStatus.DOWNLOADING, 10, "Downloading audio")
        audio_local = f"{job_path}/voice.mp3"
        if not download_asset(audio_url, audio_local, "Audio"):
            update_job_status(job_id, JobStatus.FAILED, 10, "Failed to download audio", "Failed to download audio file")
            return

        # Get audio duration
        update_job_status(job_id, JobStatus.DOWNLOADING, 15, "Analyzing audio duration")
        try:
            duration_result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", audio_local], 
                capture_output=True, text=True, check=True
            )
            duration = float(duration_result.stdout.strip())
            logger.info(f"Job {job_id}: Audio duration: {duration:.2f} seconds")
        except (subprocess.CalledProcessError, ValueError) as e:
            update_job_status(job_id, JobStatus.FAILED, 15, "Invalid audio file", str(e))
            return
        
        # Download and validate images
        update_job_status(job_id, JobStatus.DOWNLOADING, 20, "Downloading images")
        valid_images = []
        for i, url in enumerate(image_urls):
            path = f"{job_path}/img_{i}.png"
            progress = 20 + (i + 1) * 10  # 20-50% for image downloads
            update_job_status(job_id, JobStatus.DOWNLOADING, progress, f"Downloading image {i+1}/{len(image_urls)}")
            if download_asset(url, path, f"Img_{i}"):
                valid_images.append(path)
        
        logger.info(f"Job {job_id}: Successfully downloaded {len(valid_images)} out of {len(image_urls)} images")

        if not valid_images:
            update_job_status(job_id, JobStatus.FAILED, 50, "No valid images", "No valid images could be downloaded")
            return
        
        # Duplicate images if we have fewer than 3
        while len(valid_images) < 3:
            valid_images.append(valid_images[0])
            
        logger.info(f"Job {job_id}: Using {len(valid_images)} images for rendering")

        update_job_status(job_id, JobStatus.RENDERING, 55, "Preparing render settings")
        time_per_shot = duration / 3
        wrapped_sub = "\n".join(textwrap.wrap(subtitle_text, width=38))
        clean_sub = wrapped_sub.replace("'", "").replace('"', '').replace(":", "")
        
        # Check font availability
        font_available = os.path.exists(FONT_PATH)
        logger.info(f"Job {job_id}: Font available at {FONT_PATH}: {font_available}")
        if not font_available:
            logger.warning(f"Job {job_id}: Font not found, subtitles will be disabled")

        # Create individual clips with original high-quality settings
        clip_files = []
        for i in range(3):
            progress = 60 + (i * 10)  # 60-90% for clip rendering
            update_job_status(job_id, JobStatus.RENDERING, progress, f"Rendering clip {i+1}/3")
            
            out = f"{job_path}/c_{i}.mp4"
            z = ["0.0005", "-0.0003", "0.0007"][i]
            fr = int(time_per_shot * 30)  # Restored to 30fps for quality
            
            if font_available and clean_sub.strip():
                drawtext = f",drawtext=text='{clean_sub}':fontfile={FONT_PATH}:fontcolor=white:fontsize=40:box=1:boxcolor=black@0.5:boxborderw=20:line_spacing=15:x=(w-text_w)/2:y=h-160"
            else:
                drawtext = ""
            
            # Restored original high-quality filters: 4K scale, 30fps, unsharp filter
            filters = f"scale=4000:-1,setsar=1/1,zoompan=z='1+{z}*on':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={fr}:s=1280x720:fps=30,scale=1280:720{drawtext},unsharp=3:3:1.5"
            
            logger.info(f"Job {job_id}: Rendering clip {i+1}/3 (duration: {time_per_shot:.2f}s)")
            try:
                # Restored original high-quality settings: CRF 18, default preset
                subprocess.run([
                    "ffmpeg", "-y", "-loop", "1", "-i", valid_images[i], 
                    "-filter_complex", filters, "-t", str(time_per_shot), 
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", out
                ], check=True, capture_output=True, text=True)
                clip_files.append(out)
                logger.info(f"Job {job_id}: Clip {i+1}/3 completed")
            except subprocess.CalledProcessError as e:
                logger.error(f"Job {job_id}: FFmpeg failed for clip {i+1} - {str(e)}")
                update_job_status(job_id, JobStatus.FAILED, progress, f"Render failed on clip {i+1}", str(e))
                return

        # Merge clips
        update_job_status(job_id, JobStatus.MERGING, 90, "Merging video clips")
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
            update_job_status(job_id, JobStatus.FAILED, 90, "Merge failed", str(e))
            return

        # Add audio and create final output
        update_job_status(job_id, JobStatus.FINALIZING, 95, "Adding audio tracks")
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
            update_job_status(job_id, JobStatus.COMPLETED, 100, "Render completed", result_file=final)
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Job {job_id}: Failed to add audio - {str(e)}")
            update_job_status(job_id, JobStatus.FAILED, 95, "Audio processing failed", str(e))
            return
    except Exception as e:
        logger.error(f"Job {job_id}: Unexpected error - {str(e)}", exc_info=True)
        update_job_status(job_id, JobStatus.FAILED, 0, "Unexpected error", str(e))
    finally:
        # Keep job info for a while but cleanup large files
        logger.info(f"Job {job_id}: Processing completed")

@app.get("/job_status/{job_id}")
async def get_job_status_endpoint(job_id: str):
    """Get status of a render job"""
    job_info = get_job_status(job_id)
    if not job_info:
        raise HTTPException(status_code=404, detail="Job not found")
    
    response = asdict(job_info)
    # Convert datetime objects to ISO strings
    response["created_at"] = job_info.created_at.isoformat()
    response["updated_at"] = job_info.updated_at.isoformat()
    response["status"] = job_info.status.value
    
    if job_info.estimated_completion:
        response["estimated_completion"] = job_info.estimated_completion.isoformat()
    
    return response

@app.get("/download/{job_id}")
async def download_result(job_id: str):
    """Download completed render result"""
    job_info = get_job_status(job_id)
    if not job_info:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job_info.status != JobStatus.COMPLETED:
        raise HTTPException(status_code=400, detail=f"Job not completed. Status: {job_info.status.value}")
    
    if not job_info.result_file or not os.path.exists(job_info.result_file):
        raise HTTPException(status_code=404, detail="Result file not found")
    
    return FileResponse(job_info.result_file, media_type="video/mp4", filename=f"scene_{job_id}.mp4")

@app.get("/jobs")
async def list_jobs():
    """List all jobs with their current status"""
    with jobs_lock:
        job_list = []
        for job_id, job_info in jobs.items():
            job_dict = {
                "job_id": job_id,
                "status": job_info.status.value,
                "progress": job_info.progress,
                "message": job_info.message,
                "created_at": job_info.created_at.isoformat(),
                "updated_at": job_info.updated_at.isoformat()
            }
            if job_info.error:
                job_dict["error"] = job_info.error
            job_list.append(job_dict)
        
        return {"jobs": job_list, "total": len(job_list)}

@app.post("/concat")
async def concat_videos(payload: dict, background_tasks: BackgroundTasks):
    """Start async video concatenation job"""
    job_id = str(uuid.uuid4())
    create_job(job_id)
    
    videos = payload.get("videos", [])
    if not videos:
        update_job_status(job_id, JobStatus.FAILED, 0, "No videos provided", "videos list is required")
        raise HTTPException(status_code=400, detail="videos list is required")
    
    background_tasks.add_task(process_concat_job, job_id, payload)
    
    return JSONResponse({
        "job_id": job_id,
        "status": "pending",
        "message": f"Concat job started with {len(videos)} videos",
        "status_url": f"/job_status/{job_id}",
        "download_url": f"/download/{job_id}"
    })

def process_concat_job(job_id: str, payload: dict):
    """Background task for processing video concatenation"""
    workdir = f"{TEMP_DIR}/{job_id}"
    os.makedirs(workdir, exist_ok=True)
    
    try:
        update_job_status(job_id, JobStatus.DOWNLOADING, 10, "Starting video downloads")
        
        videos = payload.get("videos", [])
        output_name = payload.get("output_name", "final_story.mp4")
        local_files = []
        
        logger.info(f"Concat job {job_id}: Starting with {len(videos)} videos")
        
        # Download videos with progress tracking
        for i, url in enumerate(videos):
            if not url or "http" not in str(url):
                logger.warning(f"Concat job {job_id}: Skipping empty video URL at index {i}")
                continue
            
            progress = 10 + (i + 1) * 60 // len(videos)  # 10-70% for downloads
            update_job_status(job_id, JobStatus.DOWNLOADING, progress, f"Downloading video {i+1}/{len(videos)}")
            
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
            update_job_status(job_id, JobStatus.FAILED, 70, "No valid videos", "No valid scene videos found to join")
            return
        
        update_job_status(job_id, JobStatus.MERGING, 80, f"Concatenating {len(local_files)} videos")
        logger.info(f"Concat job {job_id}: Concatenating {len(local_files)} valid videos")

        # Create the FFmpeg instructions file
        list_file = f"{workdir}/list.txt"
        with open(list_file, "w") as f:
            for p in local_files:
                f.write(f"file '{os.path.abspath(p)}'\n")

        # Stitch them together
        output_path = f"{workdir}/{output_name}"
        try:
            logger.info(f"Concat job {job_id}: Running ffmpeg concat")
            subprocess.run([
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", 
                "-i", list_file, "-c", "copy", output_path
            ], check=True, capture_output=True, text=True)
            logger.info(f"Concat job {job_id}: Successfully created {output_name}")
            update_job_status(job_id, JobStatus.COMPLETED, 100, "Concatenation completed", result_file=output_path)
        except subprocess.CalledProcessError as e:
            logger.error(f"Concat job {job_id}: FFmpeg concat failed - {str(e)}")
            update_job_status(job_id, JobStatus.FAILED, 80, "FFmpeg concat failed", str(e))
            return

    except Exception as e:
        logger.error(f"Concat job {job_id}: Unexpected error - {str(e)}", exc_info=True)
        update_job_status(job_id, JobStatus.FAILED, 0, "Unexpected error", str(e))

# =====================================================
# THE ENHANCED CLEANUP SYSTEM
# =====================================================
@app.get("/cleanup")
def cleanup_system(max_age_hours: int = 1, clean_completed_jobs: bool = True):
    """Enhanced cleanup with job tracking"""
    logger.info(f"Starting cleanup of files older than {max_age_hours} hours")
    now = time.time()
    count = 0
    cleaned_jobs = 0
    
    try:
        # Cleanup old job directories
        for folder in os.listdir(TEMP_DIR):
            path = os.path.join(TEMP_DIR, folder)
            if os.path.isdir(path) and os.stat(path).st_mtime < now - (max_age_hours * 3600):
                shutil.rmtree(path, ignore_errors=True)
                count += 1
        
        # Cleanup completed jobs from memory (optional)
        if clean_completed_jobs:
            with jobs_lock:
                completed_jobs = [job_id for job_id, job_info in jobs.items() 
                                if job_info.status in [JobStatus.COMPLETED, JobStatus.FAILED] 
                                and (datetime.now() - job_info.updated_at).total_seconds() > (max_age_hours * 3600)]
                
                for job_id in completed_jobs:
                    del jobs[job_id]
                    cleaned_jobs += 1
        
        logger.info(f"Cleanup completed: removed {count} old folders, {cleaned_jobs} old job records")
        return {
            "status": "success", 
            "folders_cleared": count,
            "jobs_cleared": cleaned_jobs,
            "active_jobs": len(jobs)
        }
    except Exception as e:
        logger.error(f"Cleanup failed: {str(e)}")
        return {"status": "error", "error": str(e), "cleared": count, "jobs_cleared": cleaned_jobs}

# Health check and debug endpoints
@app.get("/")
def read_root():
    return {
        "status": "Render API is Online", 
        "version": "2.0 - Async Processing",
        "endpoints": {
            "render": "POST /render_scene_v3_subtitles - Start async video render job",
            "concat": "POST /concat - Start async video concatenation job", 
            "job_status": "GET /job_status/{job_id} - Check job progress",
            "download": "GET /download/{job_id} - Download completed result",
            "jobs": "GET /jobs - List all jobs",
            "health": "GET /health - System health check",
            "cleanup": "GET /cleanup - Clean old files and jobs"
        },
        "usage": {
            "workflow": "1. POST to render/concat → get job_id → 2. Poll job_status → 3. Download when completed",
            "timeout_solution": "Jobs now run asynchronously with full quality maintained - no more timeouts!"
        }
    }

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
        
        # Count active jobs and job directories
        active_job_dirs = len([d for d in os.listdir(TEMP_DIR) if os.path.isdir(os.path.join(TEMP_DIR, d))])
        
        # Job status summary
        with jobs_lock:
            total_jobs = len(jobs)
            job_status_counts = {}
            for status in JobStatus:
                job_status_counts[status.value] = sum(1 for job in jobs.values() if job.status == status)
        
        health_status = {
            "status": "healthy" if all([ffmpeg_available, ffprobe_available, temp_writable]) else "unhealthy",
            "ffmpeg_available": ffmpeg_available,
            "ffprobe_available": ffprobe_available,
            "font_available": font_available,
            "font_path": FONT_PATH,
            "temp_directory_writable": temp_writable,
            "temp_directory": TEMP_DIR,
            "active_job_directories": active_job_dirs,
            "total_jobs_tracked": total_jobs,
            "jobs_by_status": job_status_counts
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