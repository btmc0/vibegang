import hashlib
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

logger = logging.getLogger("cto_inv")


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_json(path: Path, data: dict) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def sha1_hex(content: bytes) -> str:
    return hashlib.sha1(content).hexdigest()


def read_text_file(path: Path) -> str:
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def write_text_file(path: Path, text: str) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as f:
        f.write(text)


def split_urls_arg(values: Optional[List[str]]) -> List[str]:
    urls: List[str] = []
    if not values:
        return urls
    for v in values:
        if not v:
            continue
        # split by comma or whitespace
        parts = re.split(r"[\s,]+", v.strip())
        urls.extend([p for p in parts if p])
    return urls


def load_urls_file(path: Path) -> List[str]:
    contents = read_text_file(path)
    urls = []
    for line in contents.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        urls.append(s)
    return urls


def is_executable(cmd: str) -> bool:
    from shutil import which

    return which(cmd) is not None


WORKSPACE_CACHE = Path(".workspace_cache")
SOL_CACHE = WORKSPACE_CACHE / "solidity"


def repo_root() -> Path:
    return Path(os.getcwd()).resolve()


def default_artifacts_dir(run_id: str) -> Path:
    return repo_root() / "artifacts" / run_id


def sanitize_filename(name: str) -> str:
    # keep it simple
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:120]


def iter_files(root: Path, exts: Iterable[str]) -> Iterable[Path]:
    for ext in exts:
        yield from root.rglob(f"*{ext}")


class CLIError(Exception):
    pass
