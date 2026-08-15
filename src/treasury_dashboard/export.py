"""Derive the static JSON the frontend reads.

The dashboard is a static page: there is no API in production. A GitHub Actions job
ingests daily and commits this file, so the frontend just fetches it. That keeps
hosting free, avoids cold starts, and puts the snapshot history in git history.

Everything the UI needs to caption its own numbers honestly — coverage, exclusions,
provenance — is exported alongside the numbers rather than hardcoded in the frontend.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import func, select

from .database import IngestionRun, Product, Snapshot
from .models import Granularity
from .queries import (
    breakdown_by_issuer,
    latest_snapshot_date,
    market_size_by_date,
    product_table,
)

DEFAULT_EXPORT_PATH = Path("frontend/public/data/dashboard.json")

# Daily points over several years is a lot of DOM for a line chart that is ~800px
# wide. Weekly sampling keeps the shape and cuts the payload by ~7x.
DEFAULT_SERIES_STRIDE_DAYS = 7


def _downsample_series(
    series: list[tuple[dt.date, float]], stride_days: int
) -> list[tuple[dt.date, float]]:
    """Keep every Nth point, always keeping the most recent one.

    The latest value is the headline figure, so it must survive downsampling even when
    the series length is not a multiple of the stride.
    """
    if stride_days <= 1 or len(series) <= 2:
        return series
    sampled = series[::stride_days]
    if sampled[-1] != series[-1]:
        sampled.append(series[-1])
    return sampled


def _chain_footprint(session, as_of_date: dt.date) -> list[dict[str, Any]]:
    """TVL by chain across all counted products — how concentrated the market is."""
    statement = (
        select(Snapshot.chain_id, func.sum(Snapshot.tvl_usd))
        .join(Product, Product.product_id == Snapshot.product_id)
        .where(
            Snapshot.snapshot_date == as_of_date,
            Snapshot.granularity == Granularity.PER_CHAIN.value,
            Snapshot.tvl_usd.is_not(None),
            Product.counts_toward_market_total == True,  # noqa: E712 — SQL, not Python
        )
        .group_by(Snapshot.chain_id)
        .order_by(func.sum(Snapshot.tvl_usd).desc())
    )
    from .database import Chain

    chain_names = {row.chain_id: row.chain_name for row in session.query(Chain).all()}
    return [
        {"chain_name": chain_names.get(chain_id, "unknown"), "tvl_usd": float(tvl)}
        for chain_id, tvl in session.execute(statement).all()
    ]


def build_dashboard_payload(
    session, series_stride_days: int = DEFAULT_SERIES_STRIDE_DAYS
) -> dict[str, Any]:
    as_of_date = latest_snapshot_date(session, source_name="defillama")
    if as_of_date is None:
        raise ValueError("No snapshots in the database; run an ingest before exporting")

    full_series = market_size_by_date(session)
    sampled_series = _downsample_series(full_series, series_stride_days)
    table_rows = product_table(session, as_of_date)

    excluded_rows = [row for row in table_rows if not row["counts_toward_market_total"]]
    # Only aggregator-measured products are summed. A manual figure is a point-in-time
    # number with no history behind it, so counting it would make the headline
    # disagree with the endpoint of the series — the chart and the number would
    # contradict each other.
    covered_rows = [
        row
        for row in table_rows
        if row["counts_toward_market_total"] and row["tvl_provenance"] == "aggregator"
    ]
    manual_rows = [row for row in table_rows if row["tvl_provenance"] == "manual"]
    uncovered_rows = [
        row
        for row in table_rows
        if row["counts_toward_market_total"] and row["tvl_provenance"] == "none"
    ]

    last_run = (
        session.query(IngestionRun).order_by(IngestionRun.run_id.desc()).first()
    )

    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "as_of_date": as_of_date.isoformat(),
        "market_total_usd": full_series[-1][1] if full_series else None,
        "history_days": len(full_series),
        # Stated explicitly so the UI never has to imply this is the whole market.
        "coverage": {
            "products_counted": len(covered_rows),
            "products_tracked": len(table_rows),
            "products_without_data": [row["symbol"] for row in uncovered_rows],
            "products_excluded_from_total": [
                {
                    "symbol": row["symbol"],
                    "tvl_usd": row["tvl_usd"],
                    "reason": row["market_total_exclusion_reason"],
                }
                for row in excluded_rows
            ],
            "products_from_manual_figures": [
                {
                    "symbol": row["symbol"],
                    "tvl_usd": row["tvl_usd"],
                    "as_of": row["tvl_as_of"].isoformat() if row["tvl_as_of"] else None,
                }
                for row in manual_rows
            ],
            "caveat": (
                "The total is the sum of tracked products, not the entire tokenized "
                "Treasury market. Products whose assets are other tracked products "
                "are excluded to avoid double counting. Products with a hand-entered "
                "figure are shown with their as-of date but not summed, since they "
                "have no historical series behind them."
            ),
        },
        "market_size_series": [
            {"date": date.isoformat(), "tvl_usd": tvl} for date, tvl in sampled_series
        ],
        "issuer_breakdown": [
            {"issuer_name": issuer, "tvl_usd": tvl}
            for issuer, tvl in breakdown_by_issuer(session, as_of_date)
        ],
        "chain_breakdown": _chain_footprint(session, as_of_date),
        "products": [
            {
                **row,
                "inception_date": (
                    row["inception_date"].isoformat() if row["inception_date"] else None
                ),
                "tvl_as_of": (
                    row["tvl_as_of"].isoformat() if row["tvl_as_of"] else None
                ),
            }
            for row in table_rows
        ],
        "last_ingestion_run": (
            {
                "source_name": last_run.source_name,
                "run_status": last_run.run_status,
                "rows_written": last_run.rows_written,
                "started_at": last_run.started_at.isoformat(),
                "error_message": last_run.error_message,
            }
            if last_run
            else None
        ),
        "data_sources": [
            {
                "name": "DefiLlama",
                "url": "https://defillama.com/rwa",
                "role": "TVL history and per-chain breakdown",
            },
            {
                "name": "DefiLlama Yields",
                "url": "https://yields.llama.fi/pools",
                "role": "7 and 30 day APY where covered",
            },
            {
                "name": "rwa.xyz",
                "url": "https://app.rwa.xyz/",
                "role": "Industry reference used to reconcile totals manually; its API "
                "is gated behind a paid plan, so it is not queried programmatically",
            },
        ],
    }


def write_dashboard_json(
    session,
    export_path: Path = DEFAULT_EXPORT_PATH,
    series_stride_days: int = DEFAULT_SERIES_STRIDE_DAYS,
) -> tuple[Path, int]:
    """Write the payload and return its path and byte size."""
    payload = build_dashboard_payload(session, series_stride_days=series_stride_days)
    export_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2, sort_keys=False)
    export_path.write_text(serialized + "\n", encoding="utf-8")
    return export_path, len(serialized)
