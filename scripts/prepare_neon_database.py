"""Create the app schema and least-privilege grants in a fresh Neon database."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
from psycopg import sql

TABLES = ("rooms", "player_sessions", "entry_rate_limits")
ROLE = "psychology_roulette_runtime"


def statements(path: Path):
    for statement in path.read_text(encoding="utf-8").split(";"):
        if statement.strip():
            yield statement


def main() -> None:
    database_url = os.getenv("PSYCHOLOGY_ROULETTE_MIGRATION_DATABASE_URL")
    expected_database = os.getenv("PSYCHOLOGY_ROULETTE_EXPECTED_DATABASE")
    runtime_password = os.getenv("PSYCHOLOGY_ROULETTE_RUNTIME_PASSWORD")
    if not database_url or not expected_database or not runtime_password:
        raise SystemExit("database preparation failed: required configuration is missing")

    root = Path(__file__).parents[1]
    try:
        with psycopg.connect(database_url, connect_timeout=10, sslmode="require") as conn:
            if conn.execute("select current_database(), current_user").fetchone() != (
                expected_database,
                "neondb_owner",
            ):
                raise ValueError("unexpected database or migration role")

            role = conn.execute(
                """
                select rolcanlogin, rolinherit, rolsuper, rolcreatedb, rolcreaterole,
                       rolreplication, rolbypassrls,
                       exists(select 1 from pg_auth_members where member=pg_roles.oid)
                from pg_roles where rolname=%s
                """,
                (ROLE,),
            ).fetchone()
            if role != (True, False, False, False, False, False, False, False):
                raise ValueError("runtime role is missing or has unexpected attributes")

            existing = conn.execute(
                """
                select table_name from information_schema.tables
                where table_schema='public' and table_name = any(%s)
                """,
                (list(TABLES),),
            ).fetchall()
            existing_names = {row[0] for row in existing}
            if existing_names and existing_names != set(TABLES):
                raise ValueError("target database has an incomplete app schema")
            if existing_names:
                has_rows = any(
                    conn.execute(sql.SQL("select exists(select 1 from public.{} limit 1)").format(
                        sql.Identifier(table)
                    )).fetchone()[0]
                    for table in TABLES
                )
                if has_rows:
                    raise ValueError("target database already contains app data")

            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    sql.SQL("alter role {} password {}").format(
                        sql.Identifier(ROLE), sql.Literal(runtime_password)
                    )
                )
                cur.execute(
                    sql.SQL("revoke all on database {} from public").format(
                        sql.Identifier(expected_database)
                    )
                )
                cur.execute(
                    sql.SQL("grant connect on database {} to {}").format(
                        sql.Identifier(expected_database), sql.Identifier(ROLE)
                    )
                )
                for filename in ("schema.sql", "neon_permissions.sql"):
                    for statement in statements(root / "database" / filename):
                        cur.execute(statement)

            checks = ", ".join(
                "has_table_privilege(%s, %s, %s)"
                for _table in TABLES
                for _privilege in ("select", "insert", "update", "delete")
            )
            table_params = [
                value
                for table in TABLES
                for value in (ROLE, f"public.{table}", "select", ROLE, f"public.{table}", "insert",
                              ROLE, f"public.{table}", "update", ROLE, f"public.{table}", "delete")
            ]
            privileges = conn.execute(
                f"select has_database_privilege(%s,current_database(),'connect'), "
                f"has_schema_privilege(%s,'public','usage'), {checks}",
                (ROLE, ROLE, *table_params),
            ).fetchone()
            if privileges is None or not all(privileges):
                raise ValueError("runtime database privileges did not verify")

    except (OSError, ValueError, psycopg.Error) as error:
        raise SystemExit(
            f"database preparation failed ({type(error).__name__}); no data was printed"
        ) from None

    print(f"Neon database prepared and runtime privileges verified ({expected_database}).")


if __name__ == "__main__":
    main()
