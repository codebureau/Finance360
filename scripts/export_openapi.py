"""Print the FastAPI OpenAPI schema as JSON. Input to the generated TypeScript client.

Usage: ``uv run python scripts/export_openapi.py > openapi.json`` (see ``npm run generate:api``).
The output is deterministic and needs no running server or data directory.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from finance360.config import Settings
from finance360.main import create_app


def main() -> int:
    settings = Settings(data_dir=Path("data"), port=8000, git_sha="dev", static_dir=None)
    json.dump(create_app(settings).openapi(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
