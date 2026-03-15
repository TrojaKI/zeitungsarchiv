"""Hugin-based panorama stitching for multi-page newspaper scans."""

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

_REQUIRED_TOOLS = [
    "pto_gen",
    "cpfind",
    "cpclean",
    "autooptimiser",
    "pano_modify",
    "hugin_executor",
]


def _check_tools() -> None:
    """Raise RuntimeError if any required Hugin tool is missing."""
    missing = [t for t in _REQUIRED_TOOLS if shutil.which(t) is None]
    if missing:
        raise RuntimeError(
            f"Hugin tools not found: {', '.join(missing)}. "
            "Install with: apt-get install hugin-tools  (or: brew install hugin)"
        )


def stitch_multipart(parts: list[Path], output: Path) -> Path:
    """Run Hugin pipeline on parts, write merged TIFF to output.

    Pipeline: pto_gen → cpfind → cpclean → autooptimiser → pano_modify → hugin_executor
    Uses a temp directory for intermediate .pto files.
    Returns output path on success, raises on failure.
    """
    if len(parts) < 2:
        raise ValueError(f"Need at least 2 parts to stitch, got {len(parts)}")

    _check_tools()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        pto = tmp_dir / "project.pto"
        output_stem = tmp_dir / output.stem

        part_strs = [str(p) for p in parts]
        log.info("Stitching %d parts → %s", len(parts), output.name)

        def run(cmd: list[str]) -> None:
            log.debug("Running: %s", " ".join(cmd))
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(
                    f"Command failed: {cmd[0]}\n"
                    f"stdout: {result.stdout}\n"
                    f"stderr: {result.stderr}"
                )

        run(["pto_gen", "--projection=0", "--fov=10", "-o", str(pto)] + part_strs)
        run(["cpfind", "--multirow", "-o", str(pto), str(pto)])
        run(["cpclean", "-o", str(pto), str(pto)])
        run(["autooptimiser", "-a", "-l", "-s", "-m", "-o", str(pto), str(pto)])
        run(["pano_modify", "--canvas=AUTO", "--crop=AUTO", "-o", str(pto), str(pto)])
        run(["hugin_executor", "--stitching", f"--prefix={output_stem}", str(pto)])

        # Hugin writes <output_stem>.tif
        stitched = Path(str(output_stem) + ".tif")
        if not stitched.exists():
            raise RuntimeError(
                f"Hugin finished but expected output not found: {stitched}"
            )

        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(stitched), output)

    log.info("Stitching complete: %s", output.name)
    return output
