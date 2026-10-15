"""Export a consistent, validated database snapshot to a new private file."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from import_snapshot import TABLES, load
from private_snapshot_file import open_private_text_file


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, help="new protected snapshot file path")
    args = parser.parse_args()
    database_url = os.getenv("PSYCHOLOGY_ROULETTE_SOURCE_DATABASE_URL")
    if not database_url:
        raise SystemExit("snapshot export failed: source database URL is required")

    try:
        import psycopg
    except ImportError:
        raise SystemExit("snapshot export failed: psycopg is required") from None

    query_path = Path(__file__).parents[1] / "database" / "export_snapshot.sql"
    try:
        query = query_path.read_text(encoding="utf-8")
        with (
            psycopg.connect(database_url, connect_timeout=10, sslmode="require") as conn,
            conn.transaction(),
            conn.cursor() as cur,
        ):
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            cur.execute(query)
            row = cur.fetchone()
            if row is None or cur.fetchone() is not None:
                raise ValueError("snapshot query returned an unexpected row count")
        document = load(json.dumps(row[0]))
        payload = json.dumps(document, separators=(",", ":"))
        destination = Path(args.file)
        try:
            with open_private_text_file(destination) as output:
                output.write(payload)
                output.write("\n")
        except OSError:
            destination.unlink(missing_ok=True)
            raise
    except (OSError, ValueError, psycopg.Error):
        raise SystemExit("snapshot export failed; no snapshot was written") from None

    summary = ", ".join(f"{table}={len(document[table])}" for table in TABLES)
    print(f"snapshot exported ({summary})")


if __name__ == "__main__":
    main()
