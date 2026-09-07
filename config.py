import os
import json as _json

from reportlab.lib import colors
from fastapi.templating import Jinja2Templates

PDF_NAVY        = colors.HexColor("#1B2A4A")
PDF_GREY_BORDER = colors.HexColor("#B7BEC9")
PDF_GREY_BG     = colors.HexColor("#EEF1F5")
PDF_GREY_TEXT   = colors.HexColor("#5A6472")
PDF_ROW_ALT     = colors.HexColor("#F7F8FA")
PDF_BODY_TEXT   = colors.HexColor("#2B2B2B")

# Anchored to this file's own location instead of the process's current
# working directory. Previously "reports"/"uploads"/etc were bare relative
# paths, so where they actually landed on disk depended on where the app
# happened to be launched from (project root locally, but possibly a
# different directory on a server via systemd/pm2/etc). Every module that
# needs one of these folders should import the path from here rather than
# hardcoding the relative name, so it's guaranteed to always resolve to the
# same place next to the project regardless of how/where the process starts.
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
DATA_DIR      = os.path.join(BASE_DIR, "data")
REPORTS_DIR   = os.path.join(BASE_DIR, "reports")
UPLOADS_DIR   = os.path.join(BASE_DIR, "uploads")
STATIC_DIR    = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
ASSETS_DIR    = os.path.join(BASE_DIR, "assets")

for _d in (DATA_DIR, REPORTS_DIR, UPLOADS_DIR, STATIC_DIR, TEMPLATES_DIR, ASSETS_DIR):
    os.makedirs(_d, exist_ok=True)

templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.filters["tojson"] = lambda obj: _json.dumps(obj)