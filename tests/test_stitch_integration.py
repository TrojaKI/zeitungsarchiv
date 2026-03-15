"""Integration test for stitch_multipart using real Hugin tools.

Requires:  hugin-tools installed (pto_gen, cpfind, cpclean,
           autooptimiser, pano_modify, hugin_executor)
Test data: examples/at_kurier_ooe_01.tif  +  examples/at_kurier_ooe_02.tif

The test is automatically skipped when any Hugin binary is missing or
when the example source files are not present.

Note: stitching real 50 MB TIFFs takes ~30–90 seconds.
"""

import shutil
import struct
from pathlib import Path

import pytest

from app.worker.stitch import _REQUIRED_TOOLS, stitch_multipart

# ---------------------------------------------------------------------------
# skip markers
# ---------------------------------------------------------------------------

_EXAMPLES = Path(__file__).parent.parent / "examples"
_PART1 = _EXAMPLES / "at_kurier_ooe_01.tif"
_PART2 = _EXAMPLES / "at_kurier_ooe_02.tif"

_hugin_missing = [t for t in _REQUIRED_TOOLS if shutil.which(t) is None]
_sources_missing = not (_PART1.exists() and _PART2.exists())

requires_hugin = pytest.mark.skipif(
    bool(_hugin_missing),
    reason=f"Hugin tools not installed: {', '.join(_hugin_missing)}",
)
requires_sources = pytest.mark.skipif(
    _sources_missing,
    reason=f"Example source TIFFs not found in {_EXAMPLES}",
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _read_tiff_dimensions(path: Path) -> tuple[int, int]:
    """Parse width and height from a TIFF header without requiring Pillow."""
    data = path.read_bytes()
    # Byte order: 'II' = little-endian, 'MM' = big-endian
    bo = data[:2]
    if bo == b"II":
        endian = "<"
    elif bo == b"MM":
        endian = ">"
    else:
        raise ValueError(f"Not a TIFF file: {path}")

    ifd_offset = struct.unpack_from(endian + "I", data, 4)[0]
    num_entries = struct.unpack_from(endian + "H", data, ifd_offset)[0]

    width = height = None
    for i in range(num_entries):
        entry_off = ifd_offset + 2 + i * 12
        tag = struct.unpack_from(endian + "H", data, entry_off)[0]
        type_ = struct.unpack_from(endian + "H", data, entry_off + 2)[0]
        value_off = entry_off + 8
        # type 3 = SHORT (uint16), type 4 = LONG (uint32)
        if type_ == 3:
            val = struct.unpack_from(endian + "H", data, value_off)[0]
        elif type_ == 4:
            val = struct.unpack_from(endian + "I", data, value_off)[0]
        else:
            continue
        if tag == 256:   # ImageWidth
            width = val
        elif tag == 257: # ImageLength
            height = val

    if width is None or height is None:
        raise ValueError(f"Could not read dimensions from TIFF: {path}")
    return width, height


# ---------------------------------------------------------------------------
# integration tests
# ---------------------------------------------------------------------------

@requires_hugin
@requires_sources
class TestStitchIntegration:
    """Full Hugin pipeline against the real example scans."""

    def test_output_file_created(self, tmp_path):
        """stitch_multipart must create the output TIFF."""
        part1 = tmp_path / "kurier_01.tif"
        part2 = tmp_path / "kurier_02.tif"
        shutil.copy(_PART1, part1)
        shutil.copy(_PART2, part2)

        output = tmp_path / "kurier_00.tif"
        result = stitch_multipart([part1, part2], output)

        assert result == output
        assert output.exists(), "stitch_multipart did not create the output file"
        assert output.stat().st_size > 0, "Output TIFF is empty"

    def test_output_is_valid_tiff(self, tmp_path):
        """Output file must start with a valid TIFF magic number."""
        part1 = tmp_path / "kurier_01.tif"
        part2 = tmp_path / "kurier_02.tif"
        shutil.copy(_PART1, part1)
        shutil.copy(_PART2, part2)

        output = tmp_path / "kurier_00.tif"
        stitch_multipart([part1, part2], output)

        magic = output.read_bytes()[:4]
        assert magic in (b"II*\x00", b"MM\x00*"), (
            f"Output is not a valid TIFF (magic bytes: {magic!r})"
        )

    def test_output_dimensions_span_both_parts(self, tmp_path):
        """Stitched panorama must be wider than either source image alone."""
        part1 = tmp_path / "kurier_01.tif"
        part2 = tmp_path / "kurier_02.tif"
        shutil.copy(_PART1, part1)
        shutil.copy(_PART2, part2)

        output = tmp_path / "kurier_00.tif"
        stitch_multipart([part1, part2], output)

        w1, _ = _read_tiff_dimensions(_PART1)
        w_out, h_out = _read_tiff_dimensions(output)

        assert w_out > 0 and h_out > 0, "Could not read output dimensions"
        assert w_out > w1, (
            f"Stitched width ({w_out}px) should exceed single-part width ({w1}px)"
        )

    def test_raises_on_too_few_parts(self, tmp_path):
        """Passing a single file must raise ValueError before touching Hugin."""
        part1 = tmp_path / "kurier_01.tif"
        shutil.copy(_PART1, part1)
        output = tmp_path / "kurier_00.tif"

        with pytest.raises(ValueError, match="at least 2 parts"):
            stitch_multipart([part1], output)
