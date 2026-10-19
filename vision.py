import base64
import subprocess
import tempfile
from pathlib import Path

from config import MODELS, model_route
from ollama_client import chat


def _encode(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _ask(prompt: str, images: list[str], num_predict: int) -> str:
    """Send one multimodal turn to whichever surface the vision role uses.

    The role is resolved rather than pinned to Ollama: the default vision model
    is hosted now, and a hardcoded local URL would keep calling a model that
    was never pulled.
    """
    base_url, api_key, surface = model_route(MODELS["vision"])
    return chat(base_url, MODELS["vision"], [{"role": "user", "content": prompt}],
                images=images, num_predict=num_predict, api_key=api_key, surface=surface)


def analyze_image(path: Path, prompt: str = "Describe this image carefully. Identify visible text, objects, layout, and important details.") -> str:
    return _ask(prompt, [_encode(path)], 2048)


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
        return _ask(prompt, [_encode(frame) for frame in frames], 2500)


def analyze_media(path: Path, prompt: str = "") -> str:
    if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
        return analyze_image(path, prompt or "Describe this image and extract any visible text.")
    if path.suffix.lower() in {".mp4", ".mov", ".mkv", ".avi", ".webm"}:
        return analyze_video(path, prompt or "Analyze this video using the representative frames. Describe what happens and extract visible text.")
    raise ValueError("Choose an image or supported video file.")
