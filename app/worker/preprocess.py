"""Image preprocessing for newspaper scans (deskew, contrast, noise reduction)."""

import logging

import cv2
import numpy as np
import pytesseract
from pathlib import Path

log = logging.getLogger(__name__)

# Archive and thumbnail output directories (overridden by settings at runtime)
ARCHIVE_DIR = Path("/app/archive")


def detect_orientation(gray: np.ndarray) -> int:
    """Detect page orientation using Tesseract OSD.

    Returns the rotation angle in degrees (90, 180, 270) needed to correct
    the image to upright, or 0 if detection fails or confidence is too low.
    """
    try:
        osd = pytesseract.image_to_osd(
            gray,
            output_type=pytesseract.Output.DICT,
            config="--psm 0",
        )
        confidence = float(osd.get("orientation_conf", 0))
        if confidence < 2.0:
            log.debug("OSD confidence too low (%.2f), skipping orientation fix", confidence)
            return 0
        rotate = int(osd.get("rotate", 0))
        if rotate not in (90, 180, 270):
            return 0
        log.info("OSD detected rotation: %d° (confidence %.2f)", rotate, confidence)
        return rotate
    except Exception as exc:
        log.debug("OSD orientation detection failed: %s", exc)
        return 0


def _deskew_angle(gray: np.ndarray) -> float:
    """Compute skew correction angle from a grayscale image."""
    coords = np.column_stack(np.where(gray < 128))
    if len(coords) < 10:
        return 0.0
    angle = cv2.minAreaRect(coords.astype(np.float32))[-1]
    if angle < -45:
        angle = 90 + angle
    return angle if abs(angle) >= 0.5 else 0.0


def _rotate(img: np.ndarray, angle: float) -> np.ndarray:
    """Rotate image by angle degrees (works for both grayscale and color)."""
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REPLICATE)


def deskew(img: np.ndarray) -> np.ndarray:
    """Correct skew via minAreaRect on dark pixel coordinates (grayscale input)."""
    angle = _deskew_angle(img)
    return _rotate(img, angle) if angle != 0.0 else img


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
    """Save image as WebP (quality 85) for the archive (color or grayscale).

    Returns a path relative to archive_dir so the URL /archive/<path> always works
    regardless of whether the app runs locally or inside Docker.
    """
    out_dir = archive_dir / source.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    abs_path = out_dir / "image.webp"
    cv2.imwrite(str(abs_path), img, [cv2.IMWRITE_WEBP_QUALITY, 85])
    return Path(source.stem) / "image.webp"   # relative to archive_dir


def save_thumbnail(img: np.ndarray, source: Path, width: int = 300,
                   archive_dir: Path = ARCHIVE_DIR) -> Path:
    """Save a JPEG thumbnail (width=300px) for the webapp list view.

    Returns a path relative to archive_dir.
    """
    h, w = img.shape[:2]
    new_h = int(h * width / w)
    thumb = cv2.resize(img, (width, new_h), interpolation=cv2.INTER_AREA)
    abs_path = archive_dir / source.stem / "thumb.jpg"
    cv2.imwrite(str(abs_path), thumb, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return Path(source.stem) / "thumb.jpg"     # relative to archive_dir


def preprocess(tiff_path: Path, archive_dir: Path = ARCHIVE_DIR) -> dict:
    """
    Full preprocessing pipeline for a TIFF scan.

    Steps:
      1. Load in color (preserves original scan colors)
      2. Convert to grayscale for analysis
      3. Deskew (angle computed from grayscale, applied to both)
      4. Normalize contrast + reduce noise (grayscale, for OCR)
      5. Save color archive WebP + thumbnail
      6. Binarize grayscale for OCR

    Returns a dict with keys: image_path, thumb_path, binary (np.ndarray).
    """
    img_color = cv2.imread(str(tiff_path), cv2.IMREAD_COLOR)
    if img_color is None:
        raise ValueError(f"Could not read image: {tiff_path}")

    # Convert 16-bit to 8-bit if needed (some TIFFs are 16-bit)
    if img_color.dtype != np.uint8:
        img_color = (img_color / 256).astype(np.uint8)

    img_gray = cv2.cvtColor(img_color, cv2.COLOR_BGR2GRAY)

    # Step 1: correct 90°/180°/270° orientation via Tesseract OSD
    rotate = detect_orientation(img_gray)
    if rotate:
        # Tesseract rotate = clockwise degrees needed; np.rot90 is CCW,
        # so invert: k = (4 - rotate/90) % 4 gives equivalent CW rotation.
        k = (4 - rotate // 90) % 4
        img_color = np.ascontiguousarray(np.rot90(img_color, k=k))
        img_gray = np.ascontiguousarray(np.rot90(img_gray, k=k))

    # Step 2: fine deskew for small residual angles (±45°)
    angle = _deskew_angle(img_gray)
    if angle != 0.0:
        img_color = _rotate(img_color, angle)
        img_gray = _rotate(img_gray, angle)

    # Contrast + noise reduction on grayscale (for OCR quality)
    img_proc = normalize_contrast(img_gray)
    img_proc = reduce_noise(img_proc)

    # Save the color image for the archive and thumbnail
    image_path = save_archive_image(img_color, tiff_path, archive_dir)
    thumb_path = save_thumbnail(img_color, tiff_path, archive_dir=archive_dir)

    binary = binarize(img_proc)

    return {
        "image_path": str(image_path),
        "thumb_path": str(thumb_path),
        "binary": binary,
    }
