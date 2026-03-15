"""Stub out heavy native dependencies so unit tests can import app modules
without requiring OpenCV, Tesseract, Ollama, or Pillow to be installed."""

import sys
import types


class _Stub:
    """Returns itself for any attribute access or call, enabling deep stub chains."""
    def __getattr__(self, _): return _Stub()
    def __call__(self, *a, **kw): return _Stub()
    def __iter__(self): return iter([])
    def __bool__(self): return False


def _make_stub(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__getattr__ = lambda attr: _Stub()  # type: ignore[method-assign]
    return mod


for _name in ("cv2", "numpy", "pytesseract", "PIL", "PIL.Image", "ollama"):
    if _name not in sys.modules:
        sys.modules[_name] = _make_stub(_name)

# numpy is used via 'import numpy as np' and np.array / np.uint8 etc.
# Provide a minimal stub so attribute lookups don't crash on import.
_np = sys.modules["numpy"]
for _attr in ("uint8", "float32", "ndarray", "array", "zeros"):
    setattr(_np, _attr, None)
