from __future__ import annotations

import hashlib
import sqlite3
from datetime import date
from pathlib import Path
from typing import Iterable

from .models import KpiSnapshot


SCHEMA = """
CREATE TABLE IF NOT EXISTS kpi_snapshots (
  id TEXT PRIMARY KEY,
  kpi_code TEXT NOT NULL,
  kpi_name TEXT NOT NULL,
  period_type TEXT NOT NULL,
  period_start DATE NOT NULL,
  period_end DATE NOT NULL,
  value REAL NOT NULL,
  unit TEXT NOT NULL,
  source TEXT NOT NULL,
  segment TEXT,
  owner TEXT,
  dimension_1 TEXT,
  dimension_2 TEXT,
  source_record_count INTEGER,
  dedupe_method TEXT,
  computed_at TIMESTAMP NOT NULL,
  hubspot_object_id TEXT,
  UNIQUE(kpi_code, period_type, period_start, segment, owner, dimension_1, dimension_2)
);

CREATE TABLE IF NOT EXISTS source_sync_runs (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  started_at TIMESTAMP NOT NULL,
  finished_at TIMESTAMP,
  status TEXT NOT NULL,
  records_read INTEGER,
  records_written INTEGER,
  error_message TEXT
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def init_db(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    connection.commit()


def snapshot_id(snapshot: KpiSnapshot) -> str:
    raw = "|".join(
        [
            snapshot.kpi_code,
            snapshot.period_type,
            snapshot.period_start.isoformat(),
            snapshot.segment or "",
            snapshot.owner or "",
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def upsert_snapshots(connection: sqlite3.Connection, snapshots: Iterable[KpiSnapshot]) -> int:
    rows = []
    for snapshot in snapshots:
        item = snapshot.with_computed_at()
        rows.append(
            (
                snapshot_id(item),
                item.kpi_code,
                item.kpi_name,
                item.period_type,
                item.period_start.isoformat(),
                item.period_end.isoformat(),
                item.value,
                item.unit,
                item.source,
                item.segment,
                item.owner,
                item.dimension_1,
                item.dimension_2,
                item.source_record_count,
                item.dedupe_method,
                item.computed_at.isoformat(),
                item.hubspot_object_id,
            )
        )
    connection.executemany(
        """
        INSERT INTO kpi_snapshots (
          id, kpi_code, kpi_name, period_type, period_start, period_end, value,
          unit, source, segment, owner, dimension_1, dimension_2,
          source_record_count, dedupe_method, computed_at, hubspot_object_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id)
        DO UPDATE SET
          kpi_name = excluded.kpi_name,
          period_type = excluded.period_type,
          period_start = excluded.period_start,
          period_end = excluded.period_end,
          value = excluded.value,
          unit = excluded.unit,
          source = excluded.source,
          segment = excluded.segment,
          owner = excluded.owner,
          dimension_1 = excluded.dimension_1,
          dimension_2 = excluded.dimension_2,
          source_record_count = excluded.source_record_count,
          dedupe_method = excluded.dedupe_method,
          computed_at = excluded.computed_at,
          hubspot_object_id = COALESCE(excluded.hubspot_object_id, kpi_snapshots.hubspot_object_id)
        """,
        rows,
    )
    connection.commit()
    return len(rows)


def latest_snapshots_for_kpi(
    connection: sqlite3.Connection,
    kpi_code: str,
    limit: int = 52,
    segment: str | None = "newsletter",
) -> list[sqlite3.Row]:
    params: list[str | int] = [kpi_code]
    where = "kpi_code = ?"
    if segment is not None:
        where += " AND segment = ?"
        params.append(segment)
    params.append(limit)
    rows = connection.execute(
        f"""
        SELECT
          kpi_code,
          kpi_name,
          period_type,
          period_start,
          period_end,
          value,
          unit,
          source,
          segment,
          dimension_1,
          dimension_2,
          computed_at
        FROM kpi_snapshots
        WHERE {where}
        ORDER BY period_start DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return list(reversed(rows))


def compact_snapshot_ids(connection: sqlite3.Connection) -> int:
    rows = connection.execute(
        """
        SELECT *
        FROM kpi_snapshots
        ORDER BY computed_at ASC
        """
    ).fetchall()
    snapshots: dict[str, sqlite3.Row] = {}
    for row in rows:
        snapshot = KpiSnapshot(
            kpi_code=row["kpi_code"],
            kpi_name=row["kpi_name"],
            period_type=row["period_type"],
            period_start=date.fromisoformat(row["period_start"]),
            period_end=date.fromisoformat(row["period_end"]),
            value=row["value"],
            unit=row["unit"],
            source=row["source"],
            segment=row["segment"],
            owner=row["owner"],
            dimension_1=row["dimension_1"],
            dimension_2=row["dimension_2"],
            source_record_count=row["source_record_count"],
            dedupe_method=row["dedupe_method"],
            hubspot_object_id=row["hubspot_object_id"],
        )
        snapshots[snapshot_id(snapshot)] = row

    connection.execute("DELETE FROM kpi_snapshots")
    kept = []
    for new_id, row in snapshots.items():
        kept.append(
            (
                new_id,
                row["kpi_code"],
                row["kpi_name"],
                row["period_type"],
                row["period_start"],
                row["period_end"],
                row["value"],
                row["unit"],
                row["source"],
                row["segment"],
                row["owner"],
                row["dimension_1"],
                row["dimension_2"],
                row["source_record_count"],
                row["dedupe_method"],
                row["computed_at"],
                row["hubspot_object_id"],
            )
        )
    connection.executemany(
        """
        INSERT INTO kpi_snapshots (
          id, kpi_code, kpi_name, period_type, period_start, period_end, value,
          unit, source, segment, owner, dimension_1, dimension_2,
          source_record_count, dedupe_method, computed_at, hubspot_object_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        kept,
    )
    connection.commit()
    return len(rows) - len(kept)
