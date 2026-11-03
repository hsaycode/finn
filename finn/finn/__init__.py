"""FINN. Keys can live in a .env file beside the code, which is gitignored, so they can't reach the repo."""
import os
from pathlib import Path


def _load_env() -> None:
    path = Path(__file__).resolve().parent.parent / ".env"
    if os.environ.get("FINN_NO_DOTENV") or not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        name, separator, value = line.partition("=")
        name = name.strip().removeprefix("export ").strip()
        if separator and name and not name.startswith("#"):
            # A real environment variable, such as one set on a host, wins over the file.
            os.environ.setdefault(name, value.strip().strip("'\""))


_load_env()
