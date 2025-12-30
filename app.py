from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
import subprocess
import requests
import os
import uuid
import shutil

def download_video(url: str, output_path: str):
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)

app = FastAPI()

@app.post("/render")
def render_scene(payload: dict):
    try:
        scene = str(payload["scene"])
        image_url = payload["image_url"]
        audio_url = payload["audio_url"]
    except KeyError:
        raise HTTPException(status_code=400, detail="Missing required fields")

    image_file = f"{scene}.png"
    audio_file = f"{scene}.mp3"
    output_file = f"{scene}.mp4"

    # Download image
    r = requests.get(image_url, timeout=60)
    if r.status_code != 200:
        raise HTTPException(400, "Failed to download image")
    with open(image_file, "wb") as f:
        f.write(r.content)

    # Download audio
    r = requests.get(audio_url, timeout=60)
    if r.status_code != 200:
        raise HTTPException(400, "Failed to download audio")
    with open(audio_file, "wb") as f:
        f.write(r.content)

    # Render video
    subprocess.run([
        "ffmpeg", "-y",
        "-loop", "1", "-i", image_file,
        "-i", audio_file,
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-pix_fmt", "yuv420p",
        "-shortest",
        output_file
    ], check=True)

    # 🚨 THIS IS THE IMPORTANT LINE 🚨
    return FileResponse(
        path=output_file,
        media_type="video/mp4",
        filename=output_file
    )

@app.post("/concat")
def concat_videos(payload: dict):
    videos = payload["videos"]
    output_name = payload.get("output_name", "final.mp4")

    job_id = str(uuid.uuid4())
    workdir = f"/tmp/{job_id}"
    os.makedirs(workdir, exist_ok=True)

    try:
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

        subprocess.run(
            [
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
            ],
            check=True,
            timeout=600
        )

        return FileResponse(
            output_path,
            media_type="video/mp4",
            filename=output_name
        )

    finally:
        shutil.rmtree(workdir, ignore_errors=True)


