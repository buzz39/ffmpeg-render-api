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
    output_name = payload.get("output_name", "final.mp4")

    job_id = str(uuid.uuid4())
    workdir = f"/tmp/{job_id}"
    os.makedirs(workdir, exist_ok=True)

    local_files = []
    for i, url in enumerate(videos):
        path = f"{workdir}/scene_{i}.mp4"
        download_video(url, path)
        local_files.append(path)

    list_file = f"{workdir}/list.txt"
    with open(list_file, "w") as f:
        for p in local_files:
            f.write(f"file '{p}'\n")

    output_path = f"{workdir}/{output_name}"

    ffmpeg([
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", list_file,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-profile:v", "main",
        "-level", "4.0",
        "-c:a", "aac",
        "-ar", "44100",
        output_path
    ])

    background_tasks.add_task(shutil.rmtree, workdir, True)

    return FileResponse(output_path, media_type="video/mp4", filename=output_name)


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
    """
    Renders ONE scene as a cinematic video.
    - Audio length drives timing
    - Multiple weighted shots
    - Silent clips → concat → mux audio once
    """

    try:
        scene = str(payload["scene"])
        image_url = payload["image_url"]
        audio_url = payload["audio_url"]
        shots = payload["shots"]
    except KeyError:
        raise HTTPException(400, "Missing required fields")

    if not shots:
        raise HTTPException(400, "Shots array required")

    job = f"/tmp/{uuid.uuid4()}"
    os.makedirs(job, exist_ok=True)

    image_path = f"{job}/image.png"
    audio_path = f"{job}/audio.mp3"
    silent_scene = f"{job}/scene_silent.mp4"
    final_scene = f"{job}/scene_final.mp4"

    download(image_url, image_path)
    download(audio_url, audio_path)

    total_audio = audio_duration(audio_path)

    total_weight = sum(s.get("weight", 1) for s in shots)
    timeline = []
    for s in shots:
        timeline.append({
            "zoom": s.get("zoom", 0.0008),
            "duration": (s.get("weight", 1) / total_weight) * total_audio
        })

    clip_files = []

    for i, shot in enumerate(timeline):
        clip = f"{job}/clip_{i}.mp4"
        clip_files.append(clip)

        frames = int(shot["duration"] * 30)

        ffmpeg([
            "ffmpeg", "-y",
            "-loop", "1", "-i", image_path,
            "-filter_complex",
            (
                "zoompan="
                f"z='1+{shot['zoom']}*on':"
                "x='iw/2-(iw/zoom/2)':"
                "y='ih/2-(ih/zoom/2)':"
                f"d={frames},"
                "fps=30,"
                "scale=1280:720"
            ),
            "-t", str(shot["duration"]),
            "-an",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            clip
        ])

    concat_list = f"{job}/list.txt"
    with open(concat_list, "w") as f:
        for c in clip_files:
            f.write(f"file '{c}'\n")

    ffmpeg([
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_list,
        "-c", "copy",
        silent_scene
    ])

    ffmpeg([
        "ffmpeg", "-y",
        "-i", silent_scene,
        "-i", audio_path,
        "-c:v", "copy",
        "-c:a", "aac",
        "-shortest",
        final_scene
    ])

    return FileResponse(
        final_scene,
        media_type="video/mp4",
        filename=f"{scene}.mp4"
    )
