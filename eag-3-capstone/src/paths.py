"""Project-root-relative path helpers, shared by all scripts."""
from pathlib import Path
from typing import Union

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def project_path(*parts: Union[str, Path]) -> Path:
    """Build an absolute path relative to the project root."""
    return PROJECT_ROOT.joinpath(*parts)


def resolve_path(path: Union[str, Path]) -> Path:
    """Resolve a CLI path against the project root, not the current directory."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    if not p.exists():
        raise FileNotFoundError(f"Not found: {path}  (looked in {p})")
    return p