"""Tesseract OCR pipeline for newspaper article scans."""

import numpy as np
import pytesseract
from pathlib import Path
from PIL import Image

from app.worker.preprocess import preprocess

# Tesseract config: automatic page segmentation with OSD
_TESS_CONFIG = "--psm 1"
_TESS_LANG = "deu"

# OCR confidence thresholds (see SKILL.md)
CONFIDENCE_GOOD = 85
CONFIDENCE_OK = 70


def _compute_confidence(ocr_data: dict) -> float:
    """Compute average word-level confidence (ignoring non-word entries)."""
    confidences = [c for c in ocr_data["conf"] if isinstance(c, (int, float)) and c > 0]
    return round(sum(confidences) / len(confidences), 1) if confidences else 0.0


def _extract_margin_text(binary: np.ndarray) -> str:
    """Run sparse-text OCR on top/bottom margin strips to capture page numbers.

    Page numbers are typically isolated in the header or footer zone and are
    missed by PSM 1 (automatic page segmentation). PSM 11 (sparse text) finds
    individual characters/words without requiring a block structure.
    """
    height = binary.shape[0]
    margin = max(int(height * 0.12), 80)  # at least 80px

    top_strip = binary[:margin, :]
    bottom_strip = binary[height - margin:, :]

    config = "--psm 11"  # sparse text: no assumed layout
    results = []
    for strip in (top_strip, bottom_strip):
        text = pytesseract.image_to_string(
            Image.fromarray(strip), lang=_TESS_LANG, config=config
        ).strip()
        if text:
            results.append(text)
    return " | ".join(results)


def run_ocr(binary: np.ndarray) -> dict:
    """
    Run Tesseract on a binarized grayscale image array.

    Returns a dict with:
      - full_text (str)
      - ocr_confidence (float, 0–100)
      - needs_review (bool)
      - meta_source (str: 'auto' or 'partial')
    """
    pil_img = Image.fromarray(binary)

    ocr_data = pytesseract.image_to_data(
        pil_img,
        lang=_TESS_LANG,
        config=_TESS_CONFIG,
        output_type=pytesseract.Output.DICT,
    )
    confidence = _compute_confidence(ocr_data)

    full_text = pytesseract.image_to_string(pil_img, lang=_TESS_LANG, config=_TESS_CONFIG)

    needs_review = confidence < CONFIDENCE_OK
    meta_source = "auto" if confidence >= CONFIDENCE_GOOD else "partial"
    margin_text = _extract_margin_text(binary)

    return {
        "full_text": full_text.strip(),
        "ocr_confidence": confidence,
        "needs_review": needs_review,
        "meta_source": meta_source,
        "margin_text": margin_text,
    }


def process_scan(tiff_path: Path, archive_dir: Path) -> dict:
    """
    Full pipeline: preprocess TIFF → OCR → result dict ready for DB insert.

    Returns a dict merging preprocessing and OCR results:
      image_path, thumb_path, full_text, ocr_confidence, needs_review, meta_source
    """
    pre = preprocess(tiff_path, archive_dir)
    ocr = run_ocr(pre["binary"])

    return {
        "image_path": pre["image_path"],
        "thumb_path": pre["thumb_path"],
        "full_text": ocr["full_text"],
        "ocr_confidence": ocr["ocr_confidence"],
        "needs_review": ocr["needs_review"],
        "meta_source": ocr["meta_source"],
        "margin_text": ocr["margin_text"],
    }
