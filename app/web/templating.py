"""Shared Jinja2Templates instance with globals injected at startup."""

import os

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/web/templates")

# Cache-bust CSS on every deploy by embedding the file's mtime as a version string.
try:
    _css_mtime = int(os.path.getmtime("app/web/static/css/custom.css"))
except OSError:
    _css_mtime = 0

templates.env.globals["css_version"] = str(_css_mtime)
