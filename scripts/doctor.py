"""Check the interpreter and SQLite build before starting the application."""

import importlib.metadata
import sqlite3
import sys
from contextlib import closing


def main() -> None:
    print(f"Python: {sys.version.split()[0]}")
    if sys.version_info < (3, 12):  # noqa: UP036 -- Diagnose unsupported interpreters too.
        raise SystemExit("Use Python 3.12 or newer; Python 3.15 is the recommended target.")
    print(f"Interpreter: {sys.executable}")
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.execute("CREATE VIRTUAL TABLE check_fts USING fts5(content)")
    print(f"SQLite: {sqlite3.sqlite_version}; FTS5 ready")
    for package in ["fastapi", "uvicorn", "python-multipart", "pypdf", "httpx"]:
        try:
            print(f"{package}: {importlib.metadata.version(package)}")
        except importlib.metadata.PackageNotFoundError:
            raise SystemExit(
                f"Missing {package}. Install requirements.txt in this environment."
            ) from None
    print("Environment ready. Ollama is optional and checked separately in the browser.")


if __name__ == "__main__":
    main()
