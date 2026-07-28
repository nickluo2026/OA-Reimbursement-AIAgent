#!/usr/bin/env python3
"""[P1/ADR-005] SQLite → MySQL 数据迁移脚本。

用法：
    # 1. 目标库建表 + 全量迁移（幂等：--truncate 先清空目标表）
    python scripts/migrate_sqlite_to_mysql.py \
        --sqlite ./oa_agent.db \
        --target "mysql+pymysql://oa:******@127.0.0.1:3306/oa?charset=utf8mb4" \
        --truncate

    # 2. 仅校验两侧行数（迁移后核对）
    python scripts/migrate_sqlite_to_mysql.py --sqlite ./oa_agent.db --target "..." --verify-only

迁移完成后，应用侧仅需设置 OA_DATABASE_URL 指向 MySQL 即可切换，业务代码零改动。
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, select, text  # noqa: E402

from skill.database import Base  # noqa: E402

BATCH_SIZE = 500


def _row_count(engine, table_name: str) -> int:
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar() or 0


def migrate(sqlite_path: str, target_url: str, truncate: bool, verify_only: bool) -> int:
    src = create_engine(f"sqlite:///{sqlite_path}")
    dst = create_engine(target_url, pool_pre_ping=True)

    # 目标库建表（checkfirst：已存在则跳过）
    if not verify_only:
        Base.metadata.create_all(dst, checkfirst=True)
        print("[1/3] 目标库建表完成")

    tables = Base.metadata.sorted_tables
    failures = 0
    for table in tables:
        src_count = _row_count(src, table.name)
        if verify_only:
            dst_count = _row_count(dst, table.name)
            flag = "✓" if src_count == dst_count else "✗"
            if src_count != dst_count:
                failures += 1
            print(f"  {flag} {table.name}: sqlite={src_count} mysql={dst_count}")
            continue

        with dst.begin() as dconn:
            if truncate:
                dconn.execute(text(f"DELETE FROM {table.name}"))
            with src.connect() as sconn:
                result = sconn.execute(select(table))
                batch = []
                moved = 0
                for row in result.mappings():
                    batch.append(dict(row))
                    if len(batch) >= BATCH_SIZE:
                        dconn.execute(table.insert(), batch)
                        moved += len(batch)
                        batch = []
                if batch:
                    dconn.execute(table.insert(), batch)
                    moved += len(batch)
        print(f"  ✓ {table.name}: 迁移 {moved} 行（源 {src_count} 行）")

    if verify_only:
        print("[校验] " + ("全部一致 ✅" if failures == 0 else f"{failures} 张表行数不一致 ❌"))
        return 1 if failures else 0

    print("[2/3] 数据迁移完成")
    print("[3/3] 建议执行 --verify-only 复核行数，并在应用侧设置：")
    print(
        f'      export OA_DATABASE_URL="{target_url.split("@")[-1] and "mysql+pymysql://<user>:<pass>@<host>/<db>?charset=utf8mb4"}"'
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="SQLite → MySQL 数据迁移")
    ap.add_argument("--sqlite", required=True, help="源 SQLite 文件路径")
    ap.add_argument("--target", required=True, help="目标 MySQL 连接串（mysql+pymysql://...）")
    ap.add_argument("--truncate", action="store_true", help="迁移前清空目标表（幂等重跑）")
    ap.add_argument("--verify-only", action="store_true", help="仅比对两侧行数，不迁移")
    args = ap.parse_args()

    if not os.path.exists(args.sqlite):
        print(f"[错误] SQLite 文件不存在: {args.sqlite}")
        return 1
    return migrate(args.sqlite, args.target, args.truncate, args.verify_only)


if __name__ == "__main__":
    raise SystemExit(main())
