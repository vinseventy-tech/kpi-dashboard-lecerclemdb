from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone


@dataclass(frozen=True)
class KpiSnapshot:
    kpi_code: str
    kpi_name: str
    period_type: str
    period_start: date
    period_end: date
    value: float
    unit: str
    source: str
    segment: str | None = None
    owner: str | None = None
    dimension_1: str | None = None
    dimension_2: str | None = None
    source_record_count: int | None = None
    dedupe_method: str | None = None
    computed_at: datetime | None = None
    hubspot_object_id: str | None = None

    def with_computed_at(self) -> "KpiSnapshot":
        if self.computed_at:
            return self
        return KpiSnapshot(
            **{
                **self.__dict__,
                "computed_at": datetime.now(timezone.utc),
            }
        )
