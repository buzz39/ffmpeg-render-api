"""
Tests to verify the /merge endpoint does NOT impact any existing endpoints.
Every existing endpoint is exercised to confirm zero regressions.
"""
import os
import time
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app import app, job_status, TEMP_DIR


@pytest.fixture(autouse=True)
def clear_job_status():
    """Reset shared job_status dict before each test."""
    job_status.clear()
    yield
    job_status.clear()


client = TestClient(app)


# ── Existing endpoint: GET / ────────────────────────────────────────────
class TestRootEndpoint:
    def test_root_returns_200(self):
        r = client.get("/")
        assert r.status_code == 200
        assert r.json() == {"status": "Render API is Online"}


# ── Existing endpoint: GET /health ──────────────────────────────────────
class TestHealthEndpoint:
    def test_health_returns_200(self):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert "status" in data


# ── Existing endpoint: GET /debug/system ────────────────────────────────
class TestDebugEndpoint:
    def test_debug_system_returns_200(self):
        r = client.get("/debug/system")
        assert r.status_code == 200
        data = r.json()
        assert "temp_dir" in data
        assert "font_path" in data


# ── Existing endpoint: GET /cleanup ─────────────────────────────────────
class TestCleanupEndpoint:
    def test_cleanup_returns_success(self):
        r = client.get("/cleanup")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "success"
        assert "cleared" in data


# ── Existing endpoint: POST /render_scene_v3_subtitles ──────────────────
class TestRenderEndpoint:
    def test_render_missing_audio_url(self):
        r = client.post("/render_scene_v3_subtitles", json={"image_urls": ["http://a.com/i.png"]})
        assert r.status_code == 400
        assert r.json()["detail"] == "audio_url is required"

    def test_render_missing_image_urls(self):
        r = client.post("/render_scene_v3_subtitles", json={"audio_url": "http://a.com/a.mp3"})
        assert r.status_code == 400
        assert r.json()["detail"] == "image_urls must be a non-empty list"

    def test_render_image_urls_not_list(self):
        r = client.post(
            "/render_scene_v3_subtitles",
            json={"audio_url": "http://a.com/a.mp3", "image_urls": "not-a-list"},
        )
        assert r.status_code == 400
        assert r.json()["detail"] == "image_urls must be a non-empty list"

    def test_render_valid_request_returns_job_id(self):
        """Valid render request should return 200 with job_id, not crash."""
        r = client.post(
            "/render_scene_v3_subtitles",
            json={
                "audio_url": "https://example.com/voice.mp3",
                "image_urls": ["https://example.com/img.png"],
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert "job_id" in data
        assert data["status"] == "processing"
        assert data["message"] == "Render job started successfully"
        assert data["check_status_url"].startswith("/job_status/")
        assert data["download_url"].startswith("/download/")


# ── Existing endpoint: POST /concat ─────────────────────────────────────
class TestConcatEndpoint:
    def test_concat_no_valid_videos(self):
        r = client.post("/concat", json={"videos": []})
        assert r.status_code == 400
        assert "No valid scene videos" in r.json()["detail"]


# ── Existing endpoint: GET /job_status/{job_id} ─────────────────────────
class TestJobStatusEndpoint:
    def test_job_status_not_found(self):
        r = client.get("/job_status/nonexistent-id-12345")
        assert r.status_code == 200
        assert r.json()["status"] == "not_found"

    def test_job_status_processing_from_memory(self):
        job_status["test-job-1"] = {"status": "processing", "message": "Downloading assets"}
        r = client.get("/job_status/test-job-1")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "processing"
        assert data["job_id"] == "test-job-1"

    def test_job_status_completed_from_memory(self):
        """Completed job with missing file should fall back to processing."""
        job_status["test-job-2"] = {
            "status": "completed",
            "message": "Done",
            "result_file": "/tmp/ffmpeg_jobs/test-job-2/final.mp4",
        }
        r = client.get("/job_status/test-job-2")
        data = r.json()
        # File doesn't actually exist, so it falls back to "processing"
        assert data["status"] == "processing"

    def test_job_status_completed_with_file(self):
        """Completed job with actual file should return completed + download_url."""
        job_id = "test-job-real"
        job_path = os.path.join(TEMP_DIR, job_id)
        os.makedirs(job_path, exist_ok=True)
        final = os.path.join(job_path, "final.mp4")
        with open(final, "wb") as f:
            f.write(b"\x00" * 1024)

        job_status[job_id] = {
            "status": "completed",
            "message": "Done",
            "result_file": final,
        }
        r = client.get(f"/job_status/{job_id}")
        data = r.json()
        assert data["status"] == "completed"
        assert data["download_url"] == f"/download/{job_id}"
        assert data["file_size_bytes"] == 1024

        # cleanup
        import shutil
        shutil.rmtree(job_path, ignore_errors=True)


# ── Existing endpoint: GET /download/{job_id} ───────────────────────────
class TestDownloadEndpoint:
    def test_download_not_found(self):
        r = client.get("/download/nonexistent-id-12345")
        assert r.status_code == 404

    def test_download_existing_file(self):
        job_id = "test-download-job"
        job_path = os.path.join(TEMP_DIR, job_id)
        os.makedirs(job_path, exist_ok=True)
        final = os.path.join(job_path, "final.mp4")
        with open(final, "wb") as f:
            f.write(b"\x00" * 2048)

        r = client.get(f"/download/{job_id}")
        assert r.status_code == 200
        assert r.headers["content-type"] == "video/mp4"
        assert len(r.content) == 2048

        import shutil
        shutil.rmtree(job_path, ignore_errors=True)


# ── New endpoint: POST /merge ───────────────────────────────────────────
class TestMergeEndpoint:
    def test_merge_missing_video_url(self):
        r = client.post("/merge", json={"audio_url": "https://example.com/a.mp4"})
        assert r.status_code == 400
        assert r.json()["detail"] == "video_url is required"

    def test_merge_missing_audio_url(self):
        r = client.post("/merge", json={"video_url": "https://example.com/v.mp4"})
        assert r.status_code == 400
        assert r.json()["detail"] == "audio_url is required"

    def test_merge_invalid_url_scheme(self):
        r = client.post(
            "/merge",
            json={
                "video_url": "ftp://example.com/v.mp4",
                "audio_url": "https://example.com/a.mp4",
            },
        )
        assert r.status_code == 400
        assert r.json()["detail"] == "Invalid URL provided"

    def test_merge_empty_body(self):
        r = client.post("/merge", json={})
        assert r.status_code == 400
        assert r.json()["detail"] == "video_url is required"

    def test_merge_valid_returns_202(self):
        r = client.post(
            "/merge",
            json={
                "video_url": "https://example.com/v.mp4",
                "audio_url": "https://example.com/a.mp4",
            },
        )
        assert r.status_code == 202
        data = r.json()
        assert "job_id" in data
        assert data["status"] == "processing"
        assert data["message"] == "Merge job started"
        assert data["check_status_url"].startswith("/job_status/")
        assert data["download_url"].startswith("/download/")


# ── Cross-endpoint isolation tests ──────────────────────────────────────
class TestCrossEndpointIsolation:
    """Verify that merge jobs and render jobs don't interfere with each other."""

    def test_render_and_merge_jobs_coexist_in_job_status(self):
        """Both job types share job_status dict. Ensure no collisions."""
        # Simulate a render job
        r1 = client.post(
            "/render_scene_v3_subtitles",
            json={
                "audio_url": "https://example.com/voice.mp3",
                "image_urls": ["https://example.com/img.png"],
            },
        )
        render_job_id = r1.json()["job_id"]

        # Simulate a merge job
        r2 = client.post(
            "/merge",
            json={
                "video_url": "https://example.com/v.mp4",
                "audio_url": "https://example.com/a.mp4",
            },
        )
        merge_job_id = r2.json()["job_id"]

        # Both jobs should exist in job_status with different IDs
        assert render_job_id != merge_job_id
        assert render_job_id in job_status
        assert merge_job_id in job_status

        # Each job's status should be independent
        s1 = client.get(f"/job_status/{render_job_id}").json()
        s2 = client.get(f"/job_status/{merge_job_id}").json()
        assert s1["job_id"] == render_job_id
        assert s2["job_id"] == merge_job_id

    def test_merge_job_uses_same_download_path_convention(self):
        """Merge outputs final.mp4, same convention as render → /download works."""
        r = client.post(
            "/merge",
            json={
                "video_url": "https://example.com/v.mp4",
                "audio_url": "https://example.com/a.mp4",
            },
        )
        job_id = r.json()["job_id"]
        # Before completion, download should 404
        dl = client.get(f"/download/{job_id}")
        assert dl.status_code == 404

    def test_existing_endpoints_still_respond_after_merge_registered(self):
        """After /merge is registered, ALL original routes still respond."""
        assert client.get("/").status_code == 200
        assert client.get("/health").status_code == 200
        assert client.get("/debug/system").status_code == 200
        assert client.get("/cleanup").status_code == 200
        assert client.get("/job_status/fake").status_code == 200
        assert client.get("/download/fake").status_code == 404
        assert client.post("/render_scene_v3_subtitles", json={}).status_code == 400
        assert client.post("/concat", json={"videos": []}).status_code == 400
