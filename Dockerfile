FROM python:3.12-slim

# System dependencies: Tesseract (deu), OpenCV, ImageMagick
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-deu \
    libopencv-dev \
    python3-opencv \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/
COPY config/ ./config/

# Runtime directories (overridden by volumes in production)
RUN mkdir -p /app/inbox /app/archive /app/db

# Expose web port
EXPOSE 8000

# Start watcher subprocess + uvicorn (controlled via WATCH_INBOX env var)
CMD ["python", "-m", "app.main"]
