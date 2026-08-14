"""Tests for the static JSON the frontend consumes.

The exported file is what the live dashboard serves, so the things asserted here are
the things that would be visibly wrong on the page.
"""

import datetime as dt
import json

import pytest

from treasury_dashboard.export import _downsample_series, build_dashboard_payload
from treasury_dashboard.pipeline import persist_snapshots
from treasury_dashboard.sources import defillama


def test_export_without_data_raises_rather_than_writing_an_empty_page(session):
    with pytest.raises(ValueError, match="run an ingest"):
        build_dashboard_payload(session)


def test_payload_shape_and_totals(session, protocol_detail, test_registry):
    product = test_registry.products[0]
    persist_snapshots(
        session, defillama.normalize_protocol_history(protocol_detail, product)
    )

    payload = build_dashboard_payload(session, series_stride_days=1)

    assert payload["as_of_date"] == "2026-05-03"
    assert payload["market_total_usd"] == 1_600_000_000.0
    assert payload["history_days"] == 3
    assert [point["date"] for point in payload["market_size_series"]] == [
        "2026-05-01",
        "2026-05-02",
        "2026-05-03",
    ]
    assert payload["issuer_breakdown"] == [
        {"issuer_name": "Test Asset Manager", "tvl_usd": 1_600_000_000.0}
    ]
    assert {row["chain_name"] for row in payload["chain_breakdown"]} == {
        "Ethereum",
        "Polygon",
    }


def test_issuer_slices_sum_to_the_headline(session, protocol_detail, test_registry):
    """A pie chart that disagrees with the headline number is worse than no chart."""
    product = test_registry.products[0]
    persist_snapshots(
        session, defillama.normalize_protocol_history(protocol_detail, product)
    )

    payload = build_dashboard_payload(session)
    slice_sum = sum(slice_["tvl_usd"] for slice_ in payload["issuer_breakdown"])

    assert slice_sum == payload["market_total_usd"]


def test_coverage_block_names_products_without_data(
    session, protocol_detail, test_registry
):
    """The UI has to be able to say what it is missing."""
    product = test_registry.products[0]
    persist_snapshots(
        session, defillama.normalize_protocol_history(protocol_detail, product)
    )

    coverage = build_dashboard_payload(session)["coverage"]

    assert coverage["products_counted"] == 1
    assert coverage["products_tracked"] == 2
    assert coverage["products_without_data"] == ["ACCRUER"]
    assert "not the entire tokenized Treasury market" in coverage["caveat"]


def test_payload_is_json_serializable(session, protocol_detail, test_registry):
    """Dates and Decimals leaking into the payload would fail at write time, after a
    successful-looking ingest."""
    product = test_registry.products[0]
    persist_snapshots(
        session, defillama.normalize_protocol_history(protocol_detail, product)
    )

    round_tripped = json.loads(json.dumps(build_dashboard_payload(session)))

    assert round_tripped["as_of_date"] == "2026-05-03"


def test_downsampling_always_keeps_the_latest_point():
    """The last point is the headline figure, so it must survive any stride."""
    series = [(dt.date(2026, 5, 1) + dt.timedelta(days=offset), float(offset)) for offset in range(10)]

    sampled = _downsample_series(series, 7)

    assert sampled[0] == series[0]
    assert sampled[-1] == series[-1]
    assert len(sampled) < len(series)


def test_downsampling_is_a_noop_for_short_series():
    series = [(dt.date(2026, 5, 1), 1.0), (dt.date(2026, 5, 2), 2.0)]

    assert _downsample_series(series, 7) == series


def test_stale_database_gives_an_actionable_error(tmp_path):
    """A database created before a schema change must fail with instructions, not a
    bare 'no such column' from SQLite."""
    import sqlite3

    from treasury_dashboard.database import StaleSchemaError, build_engine

    stale_path = tmp_path / "stale.sqlite"
    # A products table shaped like an older release: missing the columns added since.
    connection = sqlite3.connect(stale_path)
    connection.execute(
        "CREATE TABLE products ("
        "product_id INTEGER PRIMARY KEY, symbol TEXT, display_name TEXT, "
        "issuer_name TEXT, underlying_asset TEXT, nav_model TEXT)"
    )
    connection.commit()
    connection.close()

    with pytest.raises(StaleSchemaError, match="rm data/treasuries.sqlite"):
        build_engine(stale_path)
