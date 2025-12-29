from fastapi import FastAPI, HTTPException
import subprocess
import requests

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
    with open(image_file, "wb") as f:
        f.write(requests.get(image_url).content)

    # Download audio
    with open(audio_file, "wb") as f:
        f.write(requests.get(audio_url).content)

    # Render video
    subprocess.run([
        "ffmpeg", "-y",
        "-loop", "1", "-i", image_file,
        "-i", audio_file,
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        output_file
    ], check=True)

    return {
        "status": "ok",
        "scene": scene,
        "output": output_file
    }
