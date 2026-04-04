"""OpenCV-based image stitching for multi-part newspaper scans.

Uses ORB feature matching + homography + linear blending.
No external tools required — only OpenCV and Pillow.
"""

import logging
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

log = logging.getLogger(__name__)

_MIN_MATCHES = 10
_ORB_FEATURES = 3000
_MATCH_DISTANCE = 50


def _find_homography(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Return homography H that maps points in `a` to corresponding points in `b`.

    Raises ValueError if too few feature matches are found.
    """
    a_gray = cv2.cvtColor(a, cv2.COLOR_RGB2GRAY)
    b_gray = cv2.cvtColor(b, cv2.COLOR_RGB2GRAY)

    orb = cv2.ORB_create(nfeatures=_ORB_FEATURES)
    kp_a, des_a = orb.detectAndCompute(a_gray, None)
    kp_b, des_b = orb.detectAndCompute(b_gray, None)

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    raw_matches = matcher.match(des_a, des_b)
    good = [m for m in raw_matches if m.distance < _MATCH_DISTANCE]

    if len(good) < _MIN_MATCHES:
        raise ValueError(
            f"Too few feature matches to stitch: {len(good)} "
            f"(need at least {_MIN_MATCHES}). "
            "Images may not overlap enough."
        )

    pts_a = np.array([kp_a[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    pts_b = np.array([kp_b[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)

    H, inlier_mask = cv2.findHomography(pts_a, pts_b, cv2.RANSAC, 5.0)
    inliers = int(inlier_mask.ravel().sum())
    log.debug("Feature matching: %d good matches, %d RANSAC inliers", len(good), inliers)

    if H is None:
        raise ValueError("Homography estimation failed — images may not overlap.")

    return H


def _blend_pair(a: np.ndarray, b: np.ndarray, H: np.ndarray) -> np.ndarray:
    """Warp `b` into `a`'s coordinate system and blend overlapping region linearly.

    Returns the stitched RGB image as a uint8 array.
    """
    H_inv = np.linalg.inv(H)
    h_a, w_a = a.shape[:2]
    h_b, w_b = b.shape[:2]

    # Compute bounding box of b in a's coordinate system
    corners_b = np.array(
        [[0, 0], [w_b, 0], [w_b, h_b], [0, h_b]], dtype=np.float32
    ).reshape(-1, 1, 2)
    corners_b_in_a = cv2.perspectiveTransform(corners_b, H_inv).reshape(-1, 2)
    corners_a = np.array([[0, 0], [w_a, 0], [w_a, h_a], [0, h_a]], dtype=np.float32)

    all_corners = np.vstack([corners_a, corners_b_in_a])
    x_min, y_min = all_corners.min(axis=0)
    x_max, y_max = all_corners.max(axis=0)

    offset_x = float(max(0.0, -x_min))
    offset_y = float(max(0.0, -y_min))
    canvas_w = int(np.ceil(x_max + offset_x))
    canvas_h = int(np.ceil(y_max + offset_y))

    log.debug(
        "Canvas: %dx%d, offset=(%.1f, %.1f)", canvas_w, canvas_h, offset_x, offset_y
    )

    # Translate H_inv to account for canvas offset
    T = np.array([[1, 0, offset_x], [0, 1, offset_y], [0, 0, 1]], dtype=np.float64)
    H_to_canvas = T @ H_inv

    # Warp b into canvas coordinates
    warped_b = cv2.warpPerspective(b, H_to_canvas, (canvas_w, canvas_h))

    # Place a into canvas
    canvas_a = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
    ox = int(round(offset_x))
    oy = int(round(offset_y))
    canvas_a[oy : oy + h_a, ox : ox + w_a] = a

    # Build alpha masks
    mask_a = np.zeros((canvas_h, canvas_w), dtype=np.float32)
    mask_a[oy : oy + h_a, ox : ox + w_a] = 1.0
    mask_b = (warped_b.sum(axis=2) > 0).astype(np.float32)

    # Linear blend in the horizontal overlap band
    overlap = (mask_a > 0) & (mask_b > 0)
    alpha_a = mask_a.copy()
    if overlap.any():
        overlap_cols = np.where(overlap.any(axis=0))[0]
        x1, x2 = int(overlap_cols[0]), int(overlap_cols[-1])
        if x2 > x1:
            t = np.linspace(1.0, 0.0, x2 - x1 + 1, dtype=np.float32)
            # Apply gradient only where both images are present
            alpha_a[:, x1 : x2 + 1] = np.where(
                overlap[:, x1 : x2 + 1], t[np.newaxis, :], alpha_a[:, x1 : x2 + 1]
            )
        log.debug("Overlap band: x=%d..%d (%dpx)", x1, x2, x2 - x1)

    alpha_b = np.clip(mask_b * (1.0 - alpha_a), 0.0, 1.0)

    result = (
        canvas_a * alpha_a[:, :, np.newaxis] + warped_b * alpha_b[:, :, np.newaxis]
    ).astype(np.uint8)

    return result


def stitch_multipart(parts: list[Path], output: Path) -> Path:
    """Stitch two or more scan parts into a single TIFF using OpenCV.

    Uses ORB feature matching + RANSAC homography + linear blending.
    For more than two parts, images are merged pairwise left-to-right.

    Returns the output path on success, raises on failure.
    """
    if len(parts) < 2:
        raise ValueError(f"Need at least 2 parts to stitch, got {len(parts)}")

    log.info("Stitching %d parts → %s", len(parts), output.name)

    # Load first image, preserving DPI metadata
    first_pil = Image.open(parts[0])
    dpi = first_pil.info.get("dpi", (300.0, 300.0))
    current = np.array(first_pil.convert("RGB"))

    for i, next_path in enumerate(parts[1:], start=1):
        next_pil = Image.open(next_path)
        next_img = np.array(next_pil.convert("RGB"))
        log.info("  Merging part %d/%d: %s", i + 1, len(parts), next_path.name)

        H = _find_homography(current, next_img)
        current = _blend_pair(current, next_img, H)

    output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(current).save(str(output), dpi=dpi)
    log.info("Stitching complete: %s (%dx%d @ %s DPI)", output.name, current.shape[1], current.shape[0], dpi[0])
    return output
