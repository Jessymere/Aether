import base64
import subprocess
import tempfile
from pathlib import Path

from config import MODELS, OLLAMA_URL
from ollama_client import chat


def _encode(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def analyze_image(path: Path, prompt: str = "Describe this image carefully. Identify visible text, objects, layout, and important details.") -> str:
    return chat(OLLAMA_URL, MODELS["vision"], [{"role": "user", "content": prompt}], images=[_encode(path)], num_predict=2048)


def analyze_video(path: Path, prompt: str = "Analyze these representative video frames. Describe the sequence, visible text, objects, actions, and important changes over time.") -> str:
    with tempfile.TemporaryDirectory(prefix="local-ai-frames-") as folder:
        pattern = str(Path(folder) / "frame-%02d.jpg")
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
            "-vf", "fps=1/5,scale=768:-1", "-frames:v", "8", "-q:v", "4", pattern,
        ], check=True, timeout=120)
        frames = sorted(Path(folder).glob("frame-*.jpg"))
        if not frames:
            raise RuntimeError("FFmpeg did not extract any video frames.")
        return chat(OLLAMA_URL, MODELS["vision"], [{"role": "user", "content": prompt}], images=[_encode(frame) for frame in frames], num_predict=2500)


def analyze_media(path: Path, prompt: str = "") -> str:
    if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
        return analyze_image(path, prompt or "Describe this image and extract any visible text.")
    if path.suffix.lower() in {".mp4", ".mov", ".mkv", ".avi", ".webm"}:
        return analyze_video(path, prompt or "Analyze this video using the representative frames. Describe what happens and extract visible text.")
    raise ValueError("Choose an image or supported video file.")
