"""Client profile loader and validation for ad/SEO distribution research (Phase 1).

A client profile scopes all research for one client.
Stored in workspace/clients/{client_id}/profile.json.
Data only; never invents or synthesizes a missing profile.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REQUIRED_FIELDS = (
    "client_id",
    "display_name",
    "domain",
    "geo",
    "language",
    "offer",
    "audience",
    "competitors",
    "brand_voice",
    "landing_url",
    "seed_keywords",
    "forbidden_claims",
)


def client_dir(client_id: str, root: Path | str | None = None) -> Path:
    """Return the client's isolated workspace directory: workspace/clients/{client_id}/."""
    if not client_id or not isinstance(client_id, str):
        raise ValueError("client_id must be a non-empty string")
    if root is None:
        try:
            import runtime_context as rc
            root = rc.ROOT
        except Exception:
            root = Path.cwd()
    return (Path(root) / "workspace" / "clients" / client_id).resolve()


def load_client_profile(client_id: str, root: Path | str | None = None) -> dict[str, Any]:
    """Load and validate a client profile from workspace/clients/{client_id}/profile.json.

    Raises:
        ValueError("client profile not found") if the file does not exist.
        ValueError with details if schema validation fails.
    """
    if not client_id or not isinstance(client_id, str):
        raise ValueError("client profile not found")

    cdir = client_dir(client_id, root)
    profile_path = cdir / "profile.json"
    if not profile_path.is_file():
        raise ValueError("client profile not found")

    try:
        data = json.loads(profile_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"client profile is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("client profile root must be a JSON object")

    missing = [f for f in REQUIRED_FIELDS if f not in data]
    if missing:
        raise ValueError(f"client profile missing required fields: {', '.join(missing)}")

    if data.get("client_id") != client_id:
        raise ValueError(f"client profile client_id mismatch: expected {client_id!r}, found {data.get('client_id')!r}")

    for list_field in ("geo", "language", "competitors", "seed_keywords", "forbidden_claims"):
        if not isinstance(data.get(list_field), list):
            raise ValueError(f"client profile field '{list_field}' must be a list")

    return data


def save_client_profile(profile: dict[str, Any], root: Path | str | None = None) -> Path:
    """Save a client profile to workspace/clients/{client_id}/profile.json."""
    if not isinstance(profile, dict) or "client_id" not in profile:
        raise ValueError("profile must be a dictionary with a 'client_id'")
    missing = [f for f in REQUIRED_FIELDS if f not in profile]
    if missing:
        raise ValueError(f"cannot save profile missing required fields: {', '.join(missing)}")

    cdir = client_dir(profile["client_id"], root)
    cdir.mkdir(parents=True, exist_ok=True)
    profile_path = cdir / "profile.json"
    profile_path.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    return profile_path
