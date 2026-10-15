import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts" / "import_snapshot.py"
TRANSFER_SCRIPT = Path(__file__).parents[2] / "scripts" / "write_snapshot_stdin.py"

def snapshot(**overrides):
    doc = {
        "format": "psychology-roulette.postgres-snapshot.v1",
        "exported_at": "2026-01-01T00:00:00Z",
        "rooms": [],
        "player_sessions": [],
        "entry_rate_limits": [],
    }
    doc.update(overrides)
    return json.dumps(doc)

def run(payload):
    return subprocess.run(
        [sys.executable, str(SCRIPT)], input=payload, text=True, capture_output=True, env={}
    )

def test_import_requires_migration_database_url():
    result = run(snapshot())
    assert result.returncode != 0
    assert "PSYCHOLOGY_ROULETTE_MIGRATION_DATABASE_URL is required" in result.stderr

def test_import_rejects_unknown_format_without_database_access():
    result = run(
        json.dumps(
            {
                "format": "wrong",
                "exported_at": "2026-01-01T00:00:00Z",
                "rooms": [],
                "player_sessions": [],
                "entry_rate_limits": [],
            }
        )
    )
    assert result.returncode != 0
    assert "unsupported format or snapshot shape" in result.stderr

def test_import_rejects_extra_columns():
    result = run(
        snapshot(
            rooms=[
                {
                    "code": "A",
                    "state_json": {},
                    "format_version": 1,
                    "revision": "0",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                    "secret": "x",
                }
            ]
        )
    )
    assert result.returncode != 0
    assert "invalid rooms row shape" in result.stderr


def test_import_rejects_timezone_free_timestamp():
    result = run(snapshot(exported_at="2026-01-01T00:00:00"))
    assert result.returncode != 0
    assert "must include a timezone" in result.stderr


def test_import_rejects_session_without_room():
    result = run(snapshot(player_sessions=[{
        "token_hash": "a" * 64,
        "room_code": "MISSING",
        "player_id": "player-1",
        "created_at": "2026-01-01T00:00:00Z",
    }]))
    assert result.returncode != 0
    assert "session references a missing room" in result.stderr


def test_import_rejects_negative_attempt_count():
    result = run(snapshot(entry_rate_limits=[{
        "bucket_key": "ip:example",
        "window_started": "1",
        "attempts": "-1",
    }]))
    assert result.returncode != 0
    assert "invalid attempts" in result.stderr


def test_import_rejects_extra_top_level_data():
    result = run(snapshot(unexpected="value"))
    assert result.returncode != 0
    assert "unsupported format or snapshot shape" in result.stderr


def test_snapshot_transfer_creates_private_file_without_echoing_payload(tmp_path):
    destination = tmp_path / "snapshot.json"
    payload = snapshot()
    result = subprocess.run(
        [
            sys.executable,
            str(TRANSFER_SCRIPT),
            "--file",
            str(destination),
            "--terminator",
            "TRANSFER_COMPLETE",
        ],
        input=f"{payload}\nTRANSFER_COMPLETE\n",
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert "snapshot received and validated" in result.stdout
    assert payload not in result.stdout + result.stderr
    assert json.loads(destination.read_text(encoding="utf-8")) == json.loads(payload)
    if os.name == "nt":
        acl = subprocess.run(
            ["icacls.exe", str(destination)],
            text=True,
            capture_output=True,
            check=False,
        )
        assert acl.returncode == 0
        assert "(I)" not in acl.stdout
        assert acl.stdout.count(":(F)") == 3
