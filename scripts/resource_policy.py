"""Explicit disk preflight policy for P-001 execution profiles.

The Codespaces threshold is provisional and is not a claim that the full lab
fits safely. Runtime headroom is checked again before deployment.
"""
import os

STANDARD_DISK_THRESHOLD_GIB = 20.0
CODESPACES_DISK_THRESHOLD_GIB = 10.0


def disk_threshold_gib(profile: str) -> float:
    profile = profile.strip().lower()
    if profile == "standard":
        return STANDARD_DISK_THRESHOLD_GIB
    if profile == "codespaces":
        raw = os.environ.get(
            "P001_CODESPACES_MIN_FREE_DISK_GIB",
            str(CODESPACES_DISK_THRESHOLD_GIB),
        )
        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError("P001_CODESPACES_MIN_FREE_DISK_GIB must be a number") from exc
        if not 10.0 <= value <= 20.0:
            raise ValueError("Codespaces disk threshold must be between 10 and 20 GiB")
        return value
    raise ValueError(f"Unknown P001_PROFILE: {profile!r}; use standard or codespaces")
