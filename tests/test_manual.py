"""The manual source: hand-entered figures with provenance and an expiry.

This source exists for products no free API covers (BENJI). It is deliberately the
weakest one in the project, so most of what matters here is what it *refuses* to do.
"""

import datetime as dt

import pytest
from pydantic import ValidationError

from treasury_dashboard.models import Granularity, ProductSpec
from treasury_dashboard.pipeline import persist_snapshots, run_manual_ingest
from treasury_dashboard.queries import market_size_by_date, product_table
from treasury_dashboard.registry import sync_registry_to_database
from treasury_dashboard.sources import manual

TODAY = dt.date(2026, 8, 14)


def _product(**overrides) -> ProductSpec:
    defaults = {
        "symbol": "MANUALFUND",
        "display_name": "Manually Sourced Fund",
        "issuer_name": "Some Issuer",
        "underlying_asset": "US T-bills",
        "nav_model": "stable_one_dollar",
    }
    return ProductSpec.model_validate({**defaults, **overrides})


# ---------- provenance is mandatory ----------


def test_manual_figure_without_a_date_is_rejected():
    with pytest.raises(ValidationError, match="manual_tvl_as_of"):
        _product(manual_tvl_usd=1e9, manual_tvl_source_url="https://example.com")


def test_manual_figure_without_a_source_url_is_rejected():
    with pytest.raises(ValidationError, match="manual_tvl_source_url"):
        _product(manual_tvl_usd=1e9, manual_tvl_as_of="2026-08-01")


def test_manual_figure_with_full_provenance_is_accepted():
    product = _product(
        manual_tvl_usd=828_000_000.0,
        manual_tvl_as_of="2026-08-01",
        manual_tvl_source_url="https://www.franklintempleton.com/",
    )
    assert product.manual_tvl_usd == 828_000_000.0
    assert product.manual_tvl_as_of == dt.date(2026, 8, 1)


def test_manual_figure_alongside_a_live_slug_is_rejected():
    """A hand-entered number competing with an API is a second, staler answer to the
    same question."""
    with pytest.raises(ValidationError, match="Use the live source"):
        _product(
            defillama_slug="some-protocol",
            record_tvl_from_slug=True,
            manual_tvl_usd=1e9,
            manual_tvl_as_of="2026-08-01",
            manual_tvl_source_url="https://example.com",
        )


# ---------- staleness ----------


def test_fresh_figure_is_ingested():
    product = _product(
        manual_tvl_usd=828e6,
        manual_tvl_as_of="2026-08-01",
        manual_tvl_source_url="https://example.com",
    )
    snapshots, notes = manual.build_snapshots([product], TODAY)

    assert len(snapshots) == 1
    assert notes == []
    assert snapshots[0].granularity is Granularity.PRODUCT_TOTAL
    assert snapshots[0].chain_name is None
    assert snapshots[0].snapshot_date == dt.date(2026, 8, 1)
    assert snapshots[0].source_name == "issuer_manual"


def test_stale_figure_is_skipped_with_a_reason_not_silently_used():
    """A visibly missing number gets refreshed; a quietly stale one does not."""
    product = _product(
        manual_tvl_usd=828e6,
        manual_tvl_as_of="2026-01-01",
        manual_tvl_source_url="https://example.com/fobxx",
    )
    snapshots, notes = manual.build_snapshots([product], TODAY)

    assert snapshots == []
    assert len(notes) == 1
    assert "225 days old" in notes[0]
    assert "https://example.com/fobxx" in notes[0]


def test_future_dated_figure_is_rejected():
    product = _product(
        manual_tvl_usd=828e6,
        manual_tvl_as_of="2027-01-01",
        manual_tvl_source_url="https://example.com",
    )
    snapshots, notes = manual.build_snapshots([product], TODAY)

    assert snapshots == []
    assert "future" in notes[0]


def test_products_without_manual_figures_are_ignored_entirely():
    snapshots, notes = manual.build_snapshots([_product()], TODAY)
    assert (snapshots, notes) == ([], [])


@pytest.mark.parametrize(
    "as_of,expected_stale",
    [("2026-08-14", False), ("2026-06-16", False), ("2026-06-14", True)],
)
def test_staleness_boundary(as_of, expected_stale):
    assert manual.is_stale(dt.date.fromisoformat(as_of), TODAY) is expected_stale


# ---------- how manual figures reach the dashboard ----------


def test_manual_figure_shows_in_the_table_but_not_the_headline(session, test_registry):
    """The headline must equal the endpoint of the chart. A manual figure has no
    history behind it, so counting it would make the two contradict each other."""
    from treasury_dashboard.sources import defillama

    manual_product = test_registry.products[1].model_copy(
        update={
            "defillama_slug": None,
            "manual_tvl_usd": 828_000_000.0,
            "manual_tvl_as_of": dt.date(2026, 5, 2),
            "manual_tvl_source_url": "https://example.com/fobxx",
        }
    )
    registry = test_registry.model_copy(
        update={"products": [test_registry.products[0], manual_product]}
    )
    sync_registry_to_database(session, registry)

    # Aggregator data for the first product, manual for the second.
    from tests.conftest import load_fixture

    persist_snapshots(
        session,
        defillama.normalize_protocol_history(
            load_fixture("defillama_protocol_detail.json"), registry.products[0]
        ),
    )
    result = run_manual_ingest(session, registry, max_age_days=100_000)
    assert result.rows_written == 1

    rows = {row["symbol"]: row for row in product_table(session, dt.date(2026, 5, 3))}
    assert rows["ACCRUER"]["tvl_usd"] == 828_000_000.0
    assert rows["ACCRUER"]["tvl_provenance"] == "manual"
    assert rows["ACCRUER"]["tvl_as_of"] == dt.date(2026, 5, 2)

    assert rows["TESTFUND"]["tvl_provenance"] == "aggregator"

    # The headline is unchanged by the manual figure.
    assert market_size_by_date(session)[-1][1] == 1_600_000_000.0


def test_aggregator_data_wins_over_a_manual_figure(session, test_registry):
    """If a live source covers a product, its number is the one that counts."""
    from treasury_dashboard.sources import defillama
    from tests.conftest import load_fixture

    persist_snapshots(
        session,
        defillama.normalize_protocol_history(
            load_fixture("defillama_protocol_detail.json"), test_registry.products[0]
        ),
    )
    persist_snapshots(
        session,
        [
            manual.build_snapshots(
                [
                    test_registry.products[0].model_copy(
                        update={
                            "defillama_slug": None,
                            "manual_tvl_usd": 1.0,
                            "manual_tvl_as_of": dt.date(2026, 5, 3),
                            "manual_tvl_source_url": "https://example.com",
                        }
                    )
                ],
                dt.date(2026, 5, 3),
            )[0][0]
        ],
    )

    row = {r["symbol"]: r for r in product_table(session, dt.date(2026, 5, 3))}["TESTFUND"]
    assert row["tvl_provenance"] == "aggregator"
    assert row["tvl_usd"] == 1_600_000_000.0
