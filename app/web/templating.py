"""Shared Jinja2Templates instance with custom filters."""

import json
from pathlib import Path

from fastapi.templating import Jinja2Templates

_TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# Parse a JSON string to a Python list/dict; returns [] on null/error
templates.env.filters["from_json"] = lambda v: json.loads(v) if v else []
