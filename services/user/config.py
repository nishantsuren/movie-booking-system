"""Centralized configuration for the user service. Plain module-level
constants, no framework dependency, no imports from anywhere else in
this service -- a pure leaf module, safe for anything here to import
from without circularity.
"""
import os
from datetime import timedelta

AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() == "true"
TOKEN_TTL = timedelta(hours=24)
VALID_ROLES = ("CUSTOMER", "ADMIN")
