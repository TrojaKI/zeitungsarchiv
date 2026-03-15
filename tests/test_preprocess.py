"""Tests for orientation detection via Tesseract OSD."""

from unittest.mock import MagicMock, call, patch

import pytest

from app.worker.preprocess import detect_orientation


def _mock_osd(mock_tess, rotate: int, confidence: float) -> None:
    mock_tess.Output.DICT = "dict"
    mock_tess.image_to_osd.return_value = {
        "rotate": rotate,
        "orientation_conf": confidence,
    }


# Any object works as gray input — only passed through to the mocked pytesseract
_GRAY = MagicMock(name="gray_array")


class TestDetectOrientation:
    """detect_orientation() returns the OSD rotate angle or 0 as fallback."""

    def test_returns_90_when_confident(self):
        with patch("app.worker.preprocess.pytesseract") as mock_tess:
            _mock_osd(mock_tess, rotate=90, confidence=5.0)
            assert detect_orientation(_GRAY) == 90

    def test_returns_180_when_confident(self):
        with patch("app.worker.preprocess.pytesseract") as mock_tess:
            _mock_osd(mock_tess, rotate=180, confidence=3.5)
            assert detect_orientation(_GRAY) == 180

    def test_returns_270_when_confident(self):
        with patch("app.worker.preprocess.pytesseract") as mock_tess:
            _mock_osd(mock_tess, rotate=270, confidence=2.1)
            assert detect_orientation(_GRAY) == 270

    def test_returns_0_when_no_rotation_needed(self):
        with patch("app.worker.preprocess.pytesseract") as mock_tess:
            _mock_osd(mock_tess, rotate=0, confidence=5.0)
            assert detect_orientation(_GRAY) == 0

    def test_returns_0_on_low_confidence(self):
        with patch("app.worker.preprocess.pytesseract") as mock_tess:
            _mock_osd(mock_tess, rotate=90, confidence=1.5)
            assert detect_orientation(_GRAY) == 0

    def test_returns_0_on_exception(self):
        with patch("app.worker.preprocess.pytesseract") as mock_tess:
            mock_tess.image_to_osd.side_effect = RuntimeError("OSD failed")
            assert detect_orientation(_GRAY) == 0


class TestOrientationCorrectionApplied:
    """preprocess() must call np.rot90 with the correct k when OSD returns non-zero."""

    @pytest.mark.parametrize("rotate,expected_k", [(90, 1), (180, 2), (270, 3)])
    def test_rot90_called_with_correct_k(self, tmp_path, rotate, expected_k):
        tiff = tmp_path / "scan.tif"
        tiff.touch()

        rotated = MagicMock(name="rotated")
        contiguous = MagicMock(name="contiguous")
        contiguous.dtype = MagicMock()  # needed by cv2.cvtColor stub

        with (
            patch("app.worker.preprocess.cv2") as mock_cv2,
            patch("app.worker.preprocess.detect_orientation", return_value=rotate),
            patch("app.worker.preprocess.np.rot90", return_value=rotated) as mock_rot90,
            patch("app.worker.preprocess.np.ascontiguousarray", return_value=contiguous),
            patch("app.worker.preprocess._deskew_angle", return_value=0.0),
            patch("app.worker.preprocess.normalize_contrast", side_effect=lambda x: x),
            patch("app.worker.preprocess.reduce_noise", side_effect=lambda x: x),
            patch("app.worker.preprocess.save_archive_image", return_value="s/image.webp"),
            patch("app.worker.preprocess.save_thumbnail", return_value="s/thumb.jpg"),
            patch("app.worker.preprocess.binarize", side_effect=lambda x: x),
        ):
            from app.worker.preprocess import preprocess
            preprocess(tiff, tmp_path)

        # np.rot90 must be called with the correct k (for both color and gray image)
        calls = mock_rot90.call_args_list
        assert any(c.kwargs.get("k") == expected_k for c in calls), (
            f"Expected np.rot90(..., k={expected_k}) for rotate={rotate}, got {calls}"
        )

    def test_no_rotation_when_detect_returns_0(self, tmp_path):
        tiff = tmp_path / "scan.tif"
        tiff.touch()

        with (
            patch("app.worker.preprocess.cv2"),
            patch("app.worker.preprocess.detect_orientation", return_value=0),
            patch("app.worker.preprocess.np.rot90") as mock_rot90,
            patch("app.worker.preprocess._deskew_angle", return_value=0.0),
            patch("app.worker.preprocess.normalize_contrast", side_effect=lambda x: x),
            patch("app.worker.preprocess.reduce_noise", side_effect=lambda x: x),
            patch("app.worker.preprocess.save_archive_image", return_value="s/image.webp"),
            patch("app.worker.preprocess.save_thumbnail", return_value="s/thumb.jpg"),
            patch("app.worker.preprocess.binarize", side_effect=lambda x: x),
        ):
            from app.worker.preprocess import preprocess
            preprocess(tiff, tmp_path)

        mock_rot90.assert_not_called()
