from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from spool_house_ai.config import SilhouetteConfig
from spool_house_ai.processing.analysis import analyze_image


def create_silhouette(
    cleaned_png_path: Path,
    output_path: Path,
    config: SilhouetteConfig,
) -> np.ndarray:
    """Create a clean binary silhouette and save it as black-on-white PNG."""
    return analyze_image(cleaned_png_path, output_path, config).final_mask


def create_basic_silhouette(
    cleaned_png_path: Path,
    output_path: Path,
    config: SilhouetteConfig,
) -> np.ndarray:
    """Legacy V1 silhouette implementation kept for compatibility."""
    with Image.open(cleaned_png_path) as image:
        rgba = np.array(image.convert("RGBA"))

    alpha = rgba[:, :, 3]
    if np.any(alpha < 255):
        mask = alpha > config.threshold
    else:
        grayscale = cv2.cvtColor(rgba[:, :, :3], cv2.COLOR_RGB2GRAY)
        _, thresholded = cv2.threshold(
            grayscale,
            0,
            255,
            cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU,
        )
        mask = thresholded > 0

    if config.invert:
        mask = np.logical_not(mask)

    mask = _clean_mask(mask, config)
    output_image = np.where(mask, 0, 255).astype(np.uint8)
    Image.fromarray(output_image, mode="L").save(output_path)
    return mask


def _clean_mask(mask: np.ndarray, config: SilhouetteConfig) -> np.ndarray:
    working = mask.astype(np.uint8) * 255

    if config.blur_radius > 0:
        kernel_size = _odd_kernel_size(config.blur_radius)
        working = cv2.medianBlur(working, kernel_size)

    if config.morph_kernel_size > 0 and config.morph_iterations > 0:
        kernel_size = _odd_kernel_size(config.morph_kernel_size)
        kernel = np.ones((kernel_size, kernel_size), np.uint8)
        working = cv2.morphologyEx(
            working,
            cv2.MORPH_CLOSE,
            kernel,
            iterations=config.morph_iterations,
        )
        working = cv2.morphologyEx(
            working,
            cv2.MORPH_OPEN,
            kernel,
            iterations=config.morph_iterations,
        )

    return working > 0


def _odd_kernel_size(value: int) -> int:
    value = max(1, int(value))
    if value % 2 == 0:
        value += 1
    return value
