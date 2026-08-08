"""Normalization tests for the DefiLlama source.

These run entirely from fixtures, so the parsing layer is verified without network
access. What they do NOT verify is that the live endpoint still returns this shape —
that needs `treasury-dashboard discover-slugs` against the real API.
"""

import datetime as dt

from treasury_dashboard.models import Granularity
from treasury_dashboard.sources import defillama


def _per_chain(snapshots):
    return [s for s in snapshots if s.granularity is Granularity.PER_CHAIN]


def _product_total(snapshots):
    return [s for s in snapshots if s.granularity is Granularity.PRODUCT_TOTAL]


def test_emits_both_granularities(protocol_detail, test_registry):
    product = test_registry.products[0]
    snapshots = defillama.normalize_protocol_history(protocol_detail, product)

    # Ethereum and Polygon, three days each.
    assert len(_per_chain(snapshots)) == 6
    assert len(_product_total(snapshots)) == 3
    assert {s.source_name for s in snapshots} == {"defillama"}


def test_chain_variant_keys_are_excluded(protocol_detail, test_registry):
    """'Ethereum-staking' must not become a chain, or its TVL is counted twice."""
    product = test_registry.products[0]
    snapshots = defillama.normalize_protocol_history(protocol_detail, product)

    chain_names = {s.chain_name for s in _per_chain(snapshots)}
    assert chain_names == {"Ethereum", "Polygon"}
    assert not any(s.tvl_usd == 999999999.0 for s in snapshots)


def test_zero_and_null_tvl_points_are_dropped(protocol_detail, test_registry):
    """The Arbitrum entry in the fixture is a 0.0 and a null; recording those would
    draw a line to the floor that reads as an outflow."""
    product = test_registry.products[0]
    snapshots = defillama.normalize_protocol_history(protocol_detail, product)

    assert "Arbitrum" not in {s.chain_name for s in snapshots}


def test_dates_are_parsed_as_utc_dates(protocol_detail, test_registry):
    product = test_registry.products[0]
    snapshots = defillama.normalize_protocol_history(protocol_detail, product)

    assert {s.snapshot_date for s in snapshots} == {
        dt.date(2026, 5, 1),
        dt.date(2026, 5, 2),
        dt.date(2026, 5, 3),
    }


def test_earliest_date_limits_backfill(protocol_detail, test_registry):
    product = test_registry.products[0]
    snapshots = defillama.normalize_protocol_history(
        protocol_detail, product, earliest_date=dt.date(2026, 5, 3)
    )

    assert {s.snapshot_date for s in snapshots} == {dt.date(2026, 5, 3)}


def test_per_chain_rows_sum_close_to_product_total(protocol_detail, test_registry):
    """Sanity check on the fixture and on our key filtering: per-chain TVL for a day
    should reconcile with the reported product total."""
    product = test_registry.products[0]
    snapshots = defillama.normalize_protocol_history(protocol_detail, product)

    target_date = dt.date(2026, 5, 3)
    per_chain_sum = sum(
        s.tvl_usd for s in _per_chain(snapshots) if s.snapshot_date == target_date
    )
    reported_total = next(
        s.tvl_usd for s in _product_total(snapshots) if s.snapshot_date == target_date
    )
    assert per_chain_sum == reported_total


def test_yield_matching_prefers_the_deepest_pool(yield_pools, test_registry, snapshot_date):
    stable_product = test_registry.products[0]
    snapshots = defillama.normalize_yield_pools(
        yield_pools, [stable_product], snapshot_date
    )

    assert len(snapshots) == 1
    # 4.32 is the deep pool; 4.11 is the 25k shallow pool.
    assert snapshots[0].apy_7day == 4.32
    assert snapshots[0].apy_30day == 4.28
    assert snapshots[0].source_name == "defillama_yields"


def test_implausible_apy_is_rejected_not_stored(yield_pools, test_registry, snapshot_date):
    """The fixture pairs ACCRUER with an 812% farm pool. A Treasury product cannot
    yield that, so the row must be dropped rather than charted."""
    accruing_product = test_registry.products[1]
    snapshots = defillama.normalize_yield_pools(
        yield_pools, [accruing_product], snapshot_date
    )

    assert snapshots == []


def test_unmatched_products_get_no_apy(yield_pools, test_registry, snapshot_date):
    unmatched = test_registry.products[0].model_copy(update={"symbol": "NOPOOL"})
    assert defillama.normalize_yield_pools(yield_pools, [unmatched], snapshot_date) == []


def test_rwa_candidates_are_filtered_and_sorted():
    protocols = [
        {"slug": "small-rwa", "category": "RWA", "tvl": 100.0},
        {"slug": "a-dex", "category": "Dexes", "tvl": 9e9},
        {"slug": "big-rwa", "category": "RWA", "tvl": 5e9},
        {"slug": "no-category", "tvl": 1.0},
    ]
    candidates = defillama.find_rwa_protocol_candidates(protocols)

    assert [candidate["slug"] for candidate in candidates] == ["big-rwa", "small-rwa"]
