"""Centralized configuration for the local CDN mock. Plain module-level
constants, no framework dependency, no imports from anywhere else in
this service -- a pure leaf module, safe for anything here to import
from without circularity. Filesystem side effects (e.g. creating
STORAGE_DIR) stay in main.py -- this module only ever defines values.
"""
import os
from pathlib import Path

AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() == "true"

BASE_DIR = Path(__file__).parent
STORAGE_DIR = BASE_DIR / "storage" / "assets"
