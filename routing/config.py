"""Centralized configuration for the routing service. Plain module-level
constants, no framework dependency, no imports from anywhere else in
this service -- a pure leaf module, safe for anything here to import
from without circularity.
"""
import os

SERVICE_MAP = {
    "catalog": os.getenv("CATALOG_SERVICE_URL", "http://catalog:8000"),
    "theatre": os.getenv("THEATRE_SERVICE_URL", "http://theatre:8000"),
    "booking": os.getenv("BOOKING_SERVICE_URL", "http://booking:8000"),
    "payment": os.getenv("PAYMENT_SERVICE_URL", "http://payment:8000"),
    "user": os.getenv("USER_SERVICE_URL", "http://user:8000"),
    "agent": os.getenv("AGENT_SERVICE_URL", "http://agent:8000"),
}
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() == "true"
