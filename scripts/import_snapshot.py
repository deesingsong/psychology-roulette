"""Fail-closed import of a private JSON snapshot into an empty Postgres database."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime

TABLES = {
    "rooms": ("code", "state_json", "format_version", "revision", "created_at", "updated_at"),
    "player_sessions": ("token_hash", "room_code", "player_id", "created_at"),
    "entry_rate_limits": ("bucket_key", "window_started", "attempts"),
}


def fail(msg: str) -> None:
    raise SystemExit(f"snapshot import failed: {msg}")


def text(v, name):
    if not isinstance(v, str) or not v:
        fail(f"invalid {name}")
    return v


def timestamp(v, name):
    try:
        parsed = datetime.fromisoformat(text(v, name).replace("Z", "+00:00"))
    except ValueError:
        fail(f"invalid {name}")
    if parsed.tzinfo is None:
        fail(f"{name} must include a timezone")
    return parsed.astimezone(UTC)


def integer_string(v, name, *, minimum=None):
    if not isinstance(v, str) or not v or not v.lstrip("-").isdigit():
        fail(f"invalid {name}")
    result = int(v)
    if minimum is not None and result < minimum:
        fail(f"invalid {name}")
    return result


def load(raw):
    try:
        doc = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        fail("invalid JSON")
    expected = {"format", "exported_at", *TABLES}
    if (
        not isinstance(doc, dict)
        or set(doc) != expected
        or doc.get("format") != "psychology-roulette.postgres-snapshot.v1"
    ):
        fail("unsupported format or snapshot shape")
    timestamp(doc["exported_at"], "exported_at")
    for table, cols in TABLES.items():
        rows = doc.get(table)
        if not isinstance(rows, list):
            fail(f"{table} must be an array")
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != set(cols):
                fail(f"invalid {table} row shape")
            if table == "rooms":
                text(row["code"], "room code")
                if row["code"] in seen:
                    fail("duplicate room code")
                seen.add(row["code"])
                integer_string(row["revision"], "revision", minimum=0)
                if (
                    not isinstance(row["format_version"], int)
                    or isinstance(row["format_version"], bool)
                    or row["format_version"] < 1
                ):
                    fail("invalid format_version")
                if not isinstance(row["state_json"], dict):
                    fail("invalid state_json")
                timestamp(row["created_at"], "created_at")
                timestamp(row["updated_at"], "updated_at")
            elif table == "player_sessions":
                h = text(row["token_hash"], "token_hash")
                if len(h) != 64 or any(c not in "0123456789abcdef" for c in h):
                    fail("invalid token_hash")
                if h in seen:
                    fail("duplicate session token hash")
                seen.add(h)
                text(row["room_code"], "room_code")
                text(row["player_id"], "player_id")
                timestamp(row["created_at"], "created_at")
            else:
                key = text(row["bucket_key"], "bucket_key")
                if key in seen:
                    fail("duplicate rate-limit key")
                seen.add(key)
                integer_string(row["window_started"], "window_started", minimum=0)
                integer_string(row["attempts"], "attempts", minimum=0)
    room_codes = {row["code"] for row in doc["rooms"]}
    for row in doc["player_sessions"]:
        if row["room_code"] not in room_codes:
            fail("session references a missing room")
    return doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="path to a protected snapshot file; defaults to stdin")
    args = ap.parse_args()
    try:
        with open(args.file, encoding="utf-8") if args.file else sys.stdin as source:
            doc = load(source.read())
    except OSError:
        fail("could not read snapshot file")
    database_url = os.getenv("PSYCHOLOGY_ROULETTE_MIGRATION_DATABASE_URL")
    if not database_url:
        fail("PSYCHOLOGY_ROULETTE_MIGRATION_DATABASE_URL is required")
    try:
        import psycopg
    except ImportError:
        fail("psycopg is required for database import")
    try:
        with (
            psycopg.connect(database_url, connect_timeout=10, sslmode="require") as conn,
            conn.cursor() as cur,
        ):
            cur.execute(
                """
                select count(*) from public.rooms
                union all select count(*) from public.player_sessions
                union all select count(*) from public.entry_rate_limits
                """
            )
            if any(r[0] for r in cur.fetchall()):
                fail("target is not empty")
            for r in doc["rooms"]:
                cur.execute(
                    """
                    insert into public.rooms
                        (code,state_json,format_version,revision,created_at,updated_at)
                    values (%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        r["code"],
                        json.dumps(r["state_json"], separators=(",", ":")),
                        int(r["format_version"]),
                        int(r["revision"]),
                        timestamp(r["created_at"], "created_at"),
                        timestamp(r["updated_at"], "updated_at"),
                    ),
                )
            for r in doc["player_sessions"]:
                cur.execute(
                    """
                    insert into public.player_sessions
                        (token_hash,room_code,player_id,created_at)
                    values (decode(%s,'hex'),%s,%s,%s)
                    """,
                    (
                        r["token_hash"],
                        r["room_code"],
                        r["player_id"],
                        timestamp(r["created_at"], "created_at"),
                    ),
                )
            for r in doc["entry_rate_limits"]:
                cur.execute(
                    """
                    insert into public.entry_rate_limits
                        (bucket_key,window_started,attempts)
                    values (%s,%s,%s)
                    """,
                    (r["bucket_key"], int(r["window_started"]), int(r["attempts"])),
                )
    except SystemExit:
        raise
    except psycopg.Error:
        fail("database operation failed; transaction rolled back")
    counts = ", ".join(f"{table}={len(doc[table])}" for table in TABLES)
    print(f"snapshot imported; {counts}")


if __name__ == "__main__":
    main()
