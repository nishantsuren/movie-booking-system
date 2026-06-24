"""Env-derived config shared by both admin and customer modules. Pulled
out of main.py specifically so any admin/customer module can read
AUTH_ENABLED (or any other shared config) without importing main itself
-- main.py is the composition root that imports the routers, so a
router importing back from main would be a circular import. Mirrors
theatre's common/config.py.
"""
import os

AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() == "true"
