from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from db import connect, init_db

LATEST_SCHEMA_VERSION = 5


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[], None]


def _phase2():
    from migrate_phase2 import main
    main()


def _phase3():
    from migrate_phase3 import main
    main()


def _phase4():
    from migrate_phase4 import main
    main()


def _phase5():
    from migrate_phase5 import main
    main()


MIGRATIONS = (
    Migration(2, "中文检索、来源与条文失效覆盖", _phase2),
    Migration(3, "问题路由日志", _phase3),
    Migration(4, "项目模式与规范别名", _phase4),
    Migration(5, "项目知识库与审查记录", _phase5),
)


def _ensure_version_table():
    with connect() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations(
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
        """)


def get_schema_version() -> int:
    _ensure_version_table()
    with connect() as con:
        row = con.execute("SELECT COALESCE(MAX(version), 1) AS version FROM schema_migrations").fetchone()
        return int(row["version"])


def migrate(target_version: int = LATEST_SCHEMA_VERSION) -> int:
    """Run every missing, idempotent migration and record it only after success."""
    if target_version < 1 or target_version > LATEST_SCHEMA_VERSION:
        raise ValueError(f"不支持的 schema version：{target_version}")
    init_db()
    _ensure_version_table()
    with connect() as con:
        applied = {int(r["version"]) for r in con.execute("SELECT version FROM schema_migrations")}
    for migration in MIGRATIONS:
        if migration.version > target_version or migration.version in applied:
            continue
        migration.apply()
        with connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO schema_migrations(version,name,applied_at) VALUES(?,?,?)",
                (migration.version, migration.name, datetime.now(timezone.utc).isoformat()),
            )
    return get_schema_version()


def main():
    version = migrate()
    print(f"数据库迁移完成：schema version {version}。")


if __name__ == "__main__":
    main()
