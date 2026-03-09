---
name: ocr-pipeline
description: >
  Implementiere die OCR-Pipeline für das Zeitungsarchiv: Bildvorverarbeitung
  (deskew, Kontrast, Rauschen), Tesseract OCR auf Deutsch, Konfidenz-Messung
  und Archiv-Konvertierung. Verwende diesen Skill bei allem rund um OCR,
  Bildverarbeitung, Tesseract, Scan-Qualität und den Ingestion-Worker.
---

# Skill: OCR-Pipeline

## Abhängigkeiten

```bash
# System (Dockerfile / apt)
apt-get install -y tesseract-ocr tesseract-ocr-deu libopencv-dev

# Python
pip install pytesseract pillow opencv-python-headless
```

## Pipeline-Reihenfolge (kritisch)

```python
# app/worker/preprocess.py + ocr.py

def process_scan(tiff_path: Path) -> dict:
    """Vollständige Verarbeitung eines TIFF-Scans."""

    # 1. Laden
    img = cv2.imread(str(tiff_path), cv2.IMREAD_GRAYSCALE)

    # 2. Deskew — Schräglagen korrigieren
    img = deskew(img)

    # 3. Kontrast normalisieren (CLAHE)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    img = clahe.apply(img)

    # 4. Rauschen reduzieren
    img = cv2.medianBlur(img, 3)

    # 5. Archivbild speichern (WebP, vor Binarisierung!)
    archive_path = save_archive_image(img, tiff_path)
    thumb_path = save_thumbnail(img, tiff_path, width=300)

    # 6. Binarisierung nur für OCR
    _, binary = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # 7. OCR
    pil_img = Image.fromarray(binary)
    ocr_data = pytesseract.image_to_data(
        pil_img,
        lang="deu",
        config="--psm 1",        # Auto-Seitensegmentierung mit OSD
        output_type=pytesseract.Output.DICT
    )

    # 8. Konfidenz berechnen (Durchschnitt über Wörter > 0)
    confidences = [c for c in ocr_data["conf"] if c > 0]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0

    # 9. Volltext zusammensetzen
    full_text = pytesseract.image_to_string(pil_img, lang="deu", config="--psm 1")

    return {
        "full_text": full_text.strip(),
        "ocr_confidence": round(avg_confidence, 1),
        "needs_review": avg_confidence < 70,
        "archive_path": str(archive_path),
        "thumb_path": str(thumb_path),
    }
```

## Deskew-Implementierung

```python
def deskew(img: np.ndarray) -> np.ndarray:
    """Schräglagenkorrektur via Hough-Transformation."""
    coords = np.column_stack(np.where(img < 128))
    if len(coords) < 10:
        return img
    angle = cv2.minAreaRect(coords.astype(np.float32))[-1]
    if angle < -45:
        angle = 90 + angle
    if abs(angle) < 0.5:
        return img   # Korrektur unnötig
    h, w = img.shape
    M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REPLICATE)
```

## Archiv-Konvertierung

```python
def save_archive_image(img: np.ndarray, source: Path) -> Path:
    """TIFF → WebP (Qualität 85, gute Balance Größe/Qualität)."""
    out = ARCHIVE_DIR / source.stem
    out.mkdir(parents=True, exist_ok=True)
    path = out / "image.webp"
    cv2.imwrite(str(path), img, [cv2.IMWRITE_WEBP_QUALITY, 85])
    return path

def save_thumbnail(img: np.ndarray, source: Path, width: int = 300) -> Path:
    """Thumbnail für WebApp-Listenansicht."""
    h, w = img.shape
    new_h = int(h * width / w)
    thumb = cv2.resize(img, (width, new_h), interpolation=cv2.INTER_AREA)
    path = ARCHIVE_DIR / source.stem / "thumb.jpg"
    cv2.imwrite(str(path), thumb, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return path
```

## Konfidenz-Schwellwerte

| Konfidenz | Status | needs_review | meta_source |
|-----------|--------|-------------|-------------|
| ≥ 85%     | Gut    | 0           | auto        |
| 70–84%    | Ok     | 0           | partial     |
| < 70%     | Schlecht | 1         | partial     |

## Häufige OCR-Probleme

| Problem | Ursache | Lösung |
|---------|---------|--------|
| Schlechte Erkennung | Moiré-Raster | VueScan descreen erhöhen |
| Schiefe Zeilen | Deskew fehlgeschlagen | angle-Threshold anpassen |
| Zeichensalat | Falsche Sprache | lang="deu" prüfen |
| Leerer Text | Zu dunkles Bild | CLAHE-Parameter anpassen |

## Tesseract-Installation testen

```bash
tesseract --version
tesseract --list-langs     # muss 'deu' enthalten
echo "Test" | tesseract - - -l deu
```
