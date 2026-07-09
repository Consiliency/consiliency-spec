"""Thin Python reader for the public Consiliency canon package."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

SPEC_PACKAGE = "consiliency-spec"
SPEC_NPM_PACKAGE = "@consiliency/spec"
SPEC_VERSION = "0.1.0"
__version__ = SPEC_VERSION

_MANIFEST = "consiliency-spec.public-manifest.json"


def _source_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _read_bytes(relative_path: str) -> bytes:
    source_path = _source_root() / relative_path
    if source_path.exists():
        return source_path.read_bytes()
    data_path = resources.files(__package__).joinpath("_data", relative_path)
    return data_path.read_bytes()


def _read_text(relative_path: str) -> str:
    return _read_bytes(relative_path).decode("utf-8")


def load_manifest() -> dict[str, Any]:
    return json.loads(_read_text(_MANIFEST))


def list_public_files() -> list[str]:
    return [item["path"] for item in load_manifest()["public_files"]]


def read_public_bytes(relative_path: str) -> bytes:
    if relative_path not in set(list_public_files()):
        raise ValueError(f"Unknown public canon file: {relative_path}")
    return _read_bytes(relative_path)


def read_public_text(relative_path: str) -> str:
    return read_public_bytes(relative_path).decode("utf-8")


def load_json(relative_path: str) -> dict[str, Any]:
    return json.loads(read_public_text(relative_path))


def load_schema(name: str) -> dict[str, Any]:
    if name.endswith(".schema.json"):
        filename = name
    else:
        filename = f"{name}.schema.json"
    for path in list_public_files():
        if path.endswith(f"/{filename}"):
            return load_json(path)
    raise ValueError(f"Unknown public schema: {name}")


__all__ = [
    "SPEC_PACKAGE",
    "SPEC_NPM_PACKAGE",
    "SPEC_VERSION",
    "__version__",
    "list_public_files",
    "load_json",
    "load_manifest",
    "load_schema",
    "read_public_bytes",
    "read_public_text",
]
