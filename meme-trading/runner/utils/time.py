"""Time helpers — exists so tests can monkey-patch utc_now() if needed."""
from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
