"""Test stitch_multipart() with inbox TIFF files."""

import logging
from pathlib import Path

from app.worker.stitch import stitch_multipart

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")

inbox = Path("inbox")
parts = sorted(inbox.glob("brot_2026-03-20_0*.tif"))
output = Path("/tmp/brot_2026-03-20_stitched.tif")

print(f"Parts found: {[p.name for p in parts]}")

if len(parts) < 2:
    print("ERROR: not enough input files found")
    raise SystemExit(1)

result = stitch_multipart(parts, output)
size_mb = result.stat().st_size / 1_000_000
print(f"Output: {result}  ({size_mb:.1f} MB)")
