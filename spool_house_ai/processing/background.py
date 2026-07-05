from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from PIL import Image


def remove_background(input_path: Path, output_path: Path, enabled: bool = True) -> None:
    """Remove the image background and save a transparent PNG."""
    with Image.open(input_path) as image:
        image = image.convert("RGBA")
        if image.getextrema()[3][0] < 255:
            image.save(output_path)
            return
        if not enabled:
            image.save(output_path)
            return
        if not background_removal_available():
            image.save(output_path)
            return
        try:
            from rembg import remove
        except ImportError:
            image.save(output_path)
            return
        cleaned = remove(image) if remove is not None else image
        cleaned.save(output_path)


def background_removal_available() -> bool:
    return importlib.util.find_spec("rembg") is not None and _rembg_model_exists()


def _rembg_model_exists() -> bool:
    home = Path(os.environ.get("U2NET_HOME", Path.home() / ".u2net"))
    return any((home / model_name).exists() for model_name in ["u2net.onnx", "u2netp.onnx", "isnet-general-use.onnx"])
