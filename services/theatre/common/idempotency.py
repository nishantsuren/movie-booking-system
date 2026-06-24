"""Server-derived idempotency key (§11.1) -- see catalog/common/idempotency.py
for the full rationale; duplicated per-service rather than shared
across services, same convention as every other cross-service-repeated
helper in this codebase.
"""
import hashlib


def derive_idempotency_key(*parts: object) -> str:
    normalized = "|".join(str(p).strip().lower() for p in parts)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
