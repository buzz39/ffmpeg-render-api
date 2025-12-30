from fastapi import FastAPI, HTTPException, BackgroundTasks
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

def ffmpeg_run(cmd: list, timeout=300):
    subprocess.run(cmd, check=True, timeout=timeout)

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

    # ✅ Schedule cleanup AFTER response is sent
    background_tasks.add_task(shutil.rmtree, workdir, True)

    return FileResponse(
        output_path,
        media_type="video/mp4",
        filename=output_name
    )

@app.post("/render_cinematic")
def render_cinematic(payload: dict):
    scene = str(payload["scene"])
    image_url = payload["image_url"]
    audio_url = payload["audio_url"]

    zoom_speed = payload.get("zoom_speed", 0.0008)

    image_file = f"{scene}.png"
    audio_file = f"{scene}.mp3"
    output_file = f"{scene}_cinematic.mp4"

    requests.get(image_url, timeout=60).raise_for_status()
    open(image_file, "wb").write(requests.get(image_url).content)

    requests.get(audio_url, timeout=60).raise_for_status()
    open(audio_file, "wb").write(requests.get(audio_url).content)

    ffmpeg_run([
        "ffmpeg", "-y",
        "-loop", "1", "-i", image_file,
        "-i", audio_file,
        "-filter_complex",
        (
            f"zoompan="
            f"z='min(zoom+{zoom_speed},1.12)':"
            f"x='iw/2-(iw/zoom/2)':"
            f"y='ih/2-(ih/zoom/2)':"
            f"d=125,"
            f"scale=1280:720"
        ),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-shortest",
        output_file
    ])

    return FileResponse(output_file, media_type="video/mp4")

@app.post("/render_with_music")
def render_with_music(payload: dict):
    scene = str(payload["scene"])
    image_url = payload["image_url"]
    audio_url = payload["audio_url"]
    music_path = payload.get("music_path", "music.mp3")

    output_file = f"{scene}_music.mp4"

    ffmpeg_run([
        "ffmpeg", "-y",
        "-loop", "1", "-i", image_url,
        "-i", audio_url,
        "-stream_loop", "-1", "-i", music_path,
        "-filter_complex",
        (
            "[2:a]volume=0.15[a2];"
            "[1:a][a2]amix=inputs=2:duration=shortest[aout];"
            "zoompan=z='min(zoom+0.0008,1.1)':d=125"
        ),
        "-map", "0:v",
        "-map", "[aout]",
        "-shortest",
        output_file
    ])

    return FileResponse(output_file, media_type="video/mp4")

@app.post("/render_scene_v2")
def render_scene_v2(payload: dict):
    scene = str(payload["scene"])
    image_url = payload["image_url"]
    audio_url = payload["audio_url"]

    shots = payload.get("shots", [
        {"zoom": 0.0005, "duration": 4},
        {"zoom": 0.0010, "duration": 6},
        {"zoom": 0.0015, "duration": 5}
    ])

    image_file = f"{scene}.png"
    audio_file = f"{scene}.mp3"
    shot_files = []

    open(image_file, "wb").write(requests.get(image_url).content)
    open(audio_file, "wb").write(requests.get(audio_url).content)

    for i, shot in enumerate(shots):
        out = f"{scene}_shot_{i}.mp4"
        shot_files.append(out)

        ffmpeg_run([
            "ffmpeg", "-y",
            "-loop", "1", "-i", image_file,
            "-i", audio_file,
            "-filter_complex",
            (
                f"zoompan=z='min(zoom+{shot['zoom']},1.12)':"
                f"d={shot['duration'] * 25}"
            ),
            "-t", str(shot["duration"]),
            out
        ])

    list_file = f"{scene}_shots.txt"
    with open(list_file, "w") as f:
        for s in shot_files:
            f.write(f"file '{s}'\n")

    final_out = f"{scene}_edited.mp4"
    ffmpeg_run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", list_file,
        "-c", "copy",
        final_out
    ])

    return FileResponse(final_out, media_type="video/mp4")

@app.post("/render_hook")
def render_hook(payload: dict):
    image_url = payload["image_url"]
    text = payload["text"]

    output = "hook.mp4"

    ffmpeg_run([
        "ffmpeg", "-y",
        "-loop", "1", "-i", image_url,
        "-filter_complex",
        (
            "zoompan=z='min(zoom+0.002,1.2)':d=75,"
            f"drawtext=text='{text}':"
            "fontcolor=white:fontsize=48:"
            "box=1:boxcolor=black@0.6:"
            "x=(w-text_w)/2:y=h/2"
        ),
        "-t", "3",
        output
    ])

    return FileResponse(output, media_type="video/mp4")
