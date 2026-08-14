"""Persistence tests, including the two ways this schema could silently double count."""

import datetime as dt

from treasury_dashboard.database import Snapshot
from treasury_dashboard.models import Granularity, NormalizedSnapshot
from treasury_dashboard.pipeline import persist_snapshots
from treasury_dashboard.queries import (
    breakdown_by_issuer,
    market_size_by_date,
    product_table,
)
from treasury_dashboard.sources import defillama


def _snapshot(**overrides) -> NormalizedSnapshot:
    defaults = {
        "product_symbol": "TESTFUND",
        "chain_name": "Ethereum",
        "snapshot_date": dt.date(2026, 5, 3),
        "granularity": Granularity.PER_CHAIN,
        "tvl_usd": 1_000_000.0,
        "source_name": "defillama",
    }
    return NormalizedSnapshot(**{**defaults, **overrides})


def test_reingesting_product_totals_does_not_duplicate(session):
    """SQLite treats NULL chain_ids as distinct in a UNIQUE constraint, so a naive
    insert would append a second product-total row on every run and double the
    headline market size."""
    total = _snapshot(chain_name=None, granularity=Granularity.PRODUCT_TOTAL)

    persist_snapshots(session, [total])
    persist_snapshots(session, [total])

    total_rows = (
        session.query(Snapshot).filter(Snapshot.chain_id.is_(None)).all()
    )
    assert len(total_rows) == 1


def test_reingest_updates_in_place(session):
    persist_snapshots(session, [_snapshot(tvl_usd=1_000_000.0)])
    persist_snapshots(session, [_snapshot(tvl_usd=2_500_000.0)])

    rows = session.query(Snapshot).all()
    assert len(rows) == 1
    assert rows[0].tvl_usd == 2_500_000.0


def test_same_day_two_sources_are_kept_separately(session):
    """Two sources disagreeing is data to reconcile, not a conflict to overwrite."""
    persist_snapshots(
        session,
        [
            _snapshot(tvl_usd=1_000_000.0, source_name="defillama"),
            _snapshot(tvl_usd=1_010_000.0, source_name="onchain_rpc"),
        ],
    )

    assert session.query(Snapshot).count() == 2


def test_snapshots_for_unknown_symbols_are_dropped(session):
    written = persist_snapshots(session, [_snapshot(product_symbol="NOTINREGISTRY")])

    assert written == 0
    assert session.query(Snapshot).count() == 0


def test_unregistered_chain_from_source_is_created(session):
    persist_snapshots(session, [_snapshot(chain_name="Mantle")])

    row = session.query(Snapshot).one()
    assert row.chain_id is not None
    assert row.granularity == "per_chain"


def test_uint256_supply_survives_the_round_trip(session):
    """A large uint256 overflows SQLite's signed 64-bit INTEGER, hence TEXT storage."""
    huge_supply = 2**200
    persist_snapshots(session, [_snapshot(total_supply_raw=huge_supply)])

    stored = session.query(Snapshot).one().total_supply_raw
    assert int(stored) == huge_supply


def test_market_size_excludes_product_totals(session, protocol_detail, test_registry):
    """The load contains both granularities; the aggregate must count each product
    once."""
    product = test_registry.products[0]
    persist_snapshots(
        session, defillama.normalize_protocol_history(protocol_detail, product)
    )

    series = market_size_by_date(session)
    assert [date for date, _ in series] == [
        dt.date(2026, 5, 1),
        dt.date(2026, 5, 2),
        dt.date(2026, 5, 3),
    ]
    # Ethereum 1.28B + Polygon 0.32B on the last day. If product totals leaked in,
    # this would be 3.2B.
    assert series[-1][1] == 1_600_000_000.0


def test_excluded_products_are_absent_from_the_market_total(session, test_registry):
    """A fund-of-funds product must show its TVL in the table but not inflate the
    headline. OUSG holds BUIDL and USYC, so counting it too would double count."""
    from treasury_dashboard.registry import sync_registry_to_database

    excluded = test_registry.products[1].model_copy(
        update={
            "counts_toward_market_total": False,
            "market_total_exclusion_reason": "holds the other tracked product",
        }
    )
    sync_registry_to_database(
        session, test_registry.model_copy(update={"products": [test_registry.products[0], excluded]})
    )

    persist_snapshots(
        session,
        [
            _snapshot(product_symbol="TESTFUND", tvl_usd=1_000_000.0),
            _snapshot(product_symbol="ACCRUER", tvl_usd=400_000.0),
        ],
    )

    series = market_size_by_date(session)
    assert series[-1][1] == 1_000_000.0

    rows = {row["symbol"]: row for row in product_table(session, dt.date(2026, 5, 3))}
    assert rows["ACCRUER"]["tvl_usd"] == 400_000.0
    assert rows["ACCRUER"]["counts_toward_market_total"] is False
    assert "holds the other tracked product" in rows["ACCRUER"]["market_total_exclusion_reason"]


def test_issuer_breakdown_uses_the_same_exclusion_as_the_total(session, test_registry):
    """Slices must add up to the headline, or the pie chart contradicts the line
    chart."""
    from treasury_dashboard.registry import sync_registry_to_database

    excluded = test_registry.products[1].model_copy(
        update={
            "counts_toward_market_total": False,
            "market_total_exclusion_reason": "fund of funds",
        }
    )
    sync_registry_to_database(
        session,
        test_registry.model_copy(update={"products": [test_registry.products[0], excluded]}),
    )
    persist_snapshots(
        session,
        [
            _snapshot(product_symbol="TESTFUND", tvl_usd=1_000_000.0),
            _snapshot(product_symbol="ACCRUER", tvl_usd=400_000.0),
        ],
    )

    breakdown = breakdown_by_issuer(session, dt.date(2026, 5, 3))
    assert breakdown == [("Test Asset Manager", 1_000_000.0)]
    assert sum(tvl for _, tvl in breakdown) == market_size_by_date(session)[-1][1]


def test_breakdown_by_issuer(session, protocol_detail, test_registry):
    product = test_registry.products[0]
    persist_snapshots(
        session, defillama.normalize_protocol_history(protocol_detail, product)
    )

    breakdown = breakdown_by_issuer(session, dt.date(2026, 5, 3))
    assert breakdown == [("Test Asset Manager", 1_600_000_000.0)]


def test_product_table_keeps_products_with_no_data(session, protocol_detail, test_registry):
    """A product with no coverage must still appear, so the gap is visible in the UI
    rather than looking like the product does not exist."""
    product = test_registry.products[0]
    persist_snapshots(
        session, defillama.normalize_protocol_history(protocol_detail, product)
    )

    rows = product_table(session, dt.date(2026, 5, 3))
    by_symbol = {row["symbol"]: row for row in rows}

    assert by_symbol["TESTFUND"]["tvl_usd"] == 1_600_000_000.0
    assert by_symbol["TESTFUND"]["chain_count"] == 2
    assert by_symbol["ACCRUER"]["tvl_usd"] is None
