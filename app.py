from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
import subprocess
import requests
import os

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
    try:
        videos = payload["videos"]  # list of public URLs
    except KeyError:
        raise HTTPException(status_code=400, detail="Missing videos list")

    with open("list.txt", "w") as f:
        for url in videos:
            f.write(f"file '{url}'\n")

    subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", "list.txt",
        "-c", "copy",
        "final.mp4"
    ], check=True)

    return FileResponse(
        "final.mp4",
        media_type="video/mp4",
        filename="final.mp4"
    )

