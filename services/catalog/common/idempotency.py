"""Server-derived idempotency key (§11.1) -- shared infra, not specific
to either admin or customer routes (currently only admin create
endpoints in this service need it, since customer routes here are all
reads, but the helper itself is generic request-identity hashing, not
admin policy).
"""
import hashlib


def derive_idempotency_key(*parts: object) -> str:
    """Deterministic dedup key derived from a create request's
    identity-defining fields (§11.1) -- no client-managed Idempotency-Key
    header. Resubmitting the same logical create (a genuine retry, or an
    accidental double-submit) always re-derives the same key and is
    deduplicated automatically by the unique constraint on this column.

    Trade-off accepted deliberately: two distinct entities that happen to
    share every identity-defining field collide into one row. That's the
    cost of not requiring an explicit caller-supplied key.
    """
    normalized = "|".join(str(p).strip().lower() for p in parts)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
