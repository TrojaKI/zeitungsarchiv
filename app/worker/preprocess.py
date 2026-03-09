"""Image preprocessing for newspaper scans (deskew, contrast, noise reduction)."""

import cv2
import numpy as np
from pathlib import Path

# Archive and thumbnail output directories (overridden by settings at runtime)
ARCHIVE_DIR = Path("/app/archive")


def deskew(img: np.ndarray) -> np.ndarray:
    """Correct skew via minAreaRect on dark pixel coordinates."""
    coords = np.column_stack(np.where(img < 128))
    if len(coords) < 10:
        return img
    angle = cv2.minAreaRect(coords.astype(np.float32))[-1]
    if angle < -45:
        angle = 90 + angle
    if abs(angle) < 0.5:
        return img  # correction not needed
    h, w = img.shape
    M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REPLICATE)


def normalize_contrast(img: np.ndarray) -> np.ndarray:
    """Improve local contrast with CLAHE (avoids over-brightening flat regions)."""
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(img)


def reduce_noise(img: np.ndarray) -> np.ndarray:
    """Reduce salt-and-pepper noise with a 3x3 median filter."""
    return cv2.medianBlur(img, 3)


def binarize(img: np.ndarray) -> np.ndarray:
    """Otsu binarization — used only for OCR, not for archiving."""
    _, binary = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binary


def save_archive_image(img: np.ndarray, source: Path,
                       archive_dir: Path = ARCHIVE_DIR) -> Path:
    """Save preprocessed grayscale image as WebP (quality 85) for the archive."""
    out_dir = archive_dir / source.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "image.webp"
    cv2.imwrite(str(path), img, [cv2.IMWRITE_WEBP_QUALITY, 85])
    return path


def save_thumbnail(img: np.ndarray, source: Path, width: int = 300,
                   archive_dir: Path = ARCHIVE_DIR) -> Path:
    """Save a JPEG thumbnail (width=300px) for the webapp list view."""
    h, w = img.shape[:2]
    new_h = int(h * width / w)
    thumb = cv2.resize(img, (width, new_h), interpolation=cv2.INTER_AREA)
    path = archive_dir / source.stem / "thumb.jpg"
    cv2.imwrite(str(path), thumb, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return path


def preprocess(tiff_path: Path, archive_dir: Path = ARCHIVE_DIR) -> dict:
    """
    Full preprocessing pipeline for a TIFF scan.

    Steps:
      1. Load as grayscale
      2. Deskew
      3. Normalize contrast (CLAHE)
      4. Reduce noise (median filter)
      5. Save archive WebP + thumbnail (before binarization)
      6. Binarize for OCR

    Returns a dict with keys: image_path, thumb_path, binary (np.ndarray).
    """
    img = cv2.imread(str(tiff_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError(f"Could not read image: {tiff_path}")

    img = deskew(img)
    img = normalize_contrast(img)
    img = reduce_noise(img)

    image_path = save_archive_image(img, tiff_path, archive_dir)
    thumb_path = save_thumbnail(img, tiff_path, archive_dir=archive_dir)

    binary = binarize(img)

    return {
        "image_path": str(image_path),
        "thumb_path": str(thumb_path),
        "binary": binary,
    }
