"""Centralized configuration for the payment service. Plain module-level
constants, no framework dependency, no imports from anywhere else in
this service -- a pure leaf module, safe for anything here to import
from without circularity.
"""
import os

AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() == "true"
