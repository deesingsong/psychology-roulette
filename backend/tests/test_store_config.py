from pathlib import Path

from psychology_roulette.postgres_store import PostgresRoomStore
from psychology_roulette.store import RoomStore, configured_store


def test_configured_store_uses_sqlite_without_database_url(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "local.sqlite3"
    monkeypatch.delenv("PSYCHOLOGY_ROULETTE_DATABASE_URL", raising=False)
    monkeypatch.setenv("PSYCHOLOGY_ROULETTE_DB_PATH", str(database_path))

    store = configured_store()
    try:
        assert isinstance(store, RoomStore)
        assert store.database_path == str(database_path)
    finally:
        store.close()


def test_configured_store_uses_postgres_when_database_url_is_set(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakePool:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr("psychology_roulette.postgres_store.ConnectionPool", FakePool)
    monkeypatch.setenv(
        "PSYCHOLOGY_ROULETTE_DATABASE_URL",
        "postgresql://example.invalid/postgres?sslmode=require",
    )
    monkeypatch.setenv("PSYCHOLOGY_ROULETTE_ROOM_TTL_SECONDS", "3600")
    monkeypatch.setenv("PSYCHOLOGY_ROULETTE_REQUIRE_INVITE_TOKEN", "true")

    store = configured_store()
    try:
        assert isinstance(store, PostgresRoomStore)
        assert store.room_ttl_seconds == 3600
        assert store.require_invite_token
        assert captured["conninfo"] == (
            "postgresql://example.invalid/postgres?sslmode=require"
        )
        assert captured["min_size"] == 0
        assert captured["max_size"] == 1
        assert captured["open"] is True
        assert captured["kwargs"]["prepare_threshold"] is None
    finally:
        store.close()

    assert captured["closed"] is True
