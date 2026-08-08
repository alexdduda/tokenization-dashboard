"""DefiLlama source: the free, keyless primary for TVL history and yields.

Chosen as primary over rwa.xyz because rwa.xyz gates its API behind a $500/seat/
month Pro plan, which the project's no-paid-keys constraint rules out. See
docs/data-sources-and-schema.md.

HTTP and parsing are deliberately separate functions. The `normalize_*` functions
take plain dicts, so the whole normalization layer is testable from recorded
fixtures with no network access.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Iterable, Optional

import httpx

from ..models import Granularity, NormalizedSnapshot, ProductSpec

logger = logging.getLogger(__name__)

SOURCE_NAME = "defillama"

PROTOCOLS_URL = "https://api.llama.fi/protocols"
PROTOCOL_DETAIL_URL = "https://api.llama.fi/protocol/{slug}"
YIELD_POOLS_URL = "https://yields.llama.fi/pools"

# DefiLlama splits a chain's TVL into variants keyed like "Ethereum-borrowed" and
# "Ethereum-staking". Summing those alongside the plain "Ethereum" key would
# double count, so only unsuffixed keys are treated as chain TVL.
_CHAIN_VARIANT_SEPARATOR = "-"

# Aggregators occasionally emit a zero or a placeholder for a chain that has no
# real deployment yet. Recording those as $0 draws a line to the floor on the
# market-size chart, which looks like an outflow rather than missing data.
_MINIMUM_MEANINGFUL_TVL_USD = 1.0


def fetch_all_protocols(http_client: httpx.Client) -> list[dict[str, Any]]:
    response = http_client.get(PROTOCOLS_URL, timeout=30.0)
    response.raise_for_status()
    return response.json()


def fetch_protocol_detail(http_client: httpx.Client, slug: str) -> dict[str, Any]:
    response = http_client.get(PROTOCOL_DETAIL_URL.format(slug=slug), timeout=30.0)
    response.raise_for_status()
    return response.json()


def fetch_yield_pools(http_client: httpx.Client) -> list[dict[str, Any]]:
    response = http_client.get(YIELD_POOLS_URL, timeout=30.0)
    response.raise_for_status()
    return response.json().get("data", [])


def find_rwa_protocol_candidates(
    all_protocols: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return RWA-category protocols, largest first.

    Backs the `discover-slugs` CLI command. The registry ships with
    `defillama_slug: null` for every product on purpose: guessing slugs offline
    produces silent mismatches, so they get filled in from a live response and
    flipped to slug_verified once checked.
    """
    rwa_protocols = [
        protocol
        for protocol in all_protocols
        if (protocol.get("category") or "").upper() == "RWA"
    ]
    return sorted(rwa_protocols, key=lambda protocol: -(protocol.get("tvl") or 0.0))


def _timestamp_to_date(unix_timestamp: Any) -> Optional[dt.date]:
    try:
        return dt.datetime.fromtimestamp(int(unix_timestamp), tz=dt.timezone.utc).date()
    except (TypeError, ValueError, OSError, OverflowError):
        logger.warning("Skipping DefiLlama point with unparseable date %r", unix_timestamp)
        return None


def normalize_protocol_history(
    protocol_detail: dict[str, Any],
    product: ProductSpec,
    earliest_date: Optional[dt.date] = None,
) -> list[NormalizedSnapshot]:
    """Turn one `/protocol/{slug}` payload into per-chain and product-total rows.

    Both granularities are emitted from the same payload. They coexist safely
    because `granularity` is part of the snapshot grain, and every aggregate query
    filters on exactly one of them.
    """
    snapshots: list[NormalizedSnapshot] = []

    chain_tvls = protocol_detail.get("chainTvls") or {}
    for chain_key, chain_payload in chain_tvls.items():
        if _CHAIN_VARIANT_SEPARATOR in chain_key:
            continue
        for point in chain_payload.get("tvl") or []:
            snapshot_date = _timestamp_to_date(point.get("date"))
            if snapshot_date is None:
                continue
            if earliest_date is not None and snapshot_date < earliest_date:
                continue
            tvl_usd = point.get("totalLiquidityUSD")
            if tvl_usd is None or tvl_usd < _MINIMUM_MEANINGFUL_TVL_USD:
                continue
            snapshots.append(
                NormalizedSnapshot(
                    product_symbol=product.symbol,
                    chain_name=chain_key,
                    snapshot_date=snapshot_date,
                    granularity=Granularity.PER_CHAIN,
                    tvl_usd=float(tvl_usd),
                    source_name=SOURCE_NAME,
                )
            )

    for point in protocol_detail.get("tvl") or []:
        snapshot_date = _timestamp_to_date(point.get("date"))
        if snapshot_date is None:
            continue
        if earliest_date is not None and snapshot_date < earliest_date:
            continue
        tvl_usd = point.get("totalLiquidityUSD")
        if tvl_usd is None or tvl_usd < _MINIMUM_MEANINGFUL_TVL_USD:
            continue
        snapshots.append(
            NormalizedSnapshot(
                product_symbol=product.symbol,
                chain_name=None,
                snapshot_date=snapshot_date,
                granularity=Granularity.PRODUCT_TOTAL,
                tvl_usd=float(tvl_usd),
                source_name=SOURCE_NAME,
            )
        )

    return snapshots


def normalize_yield_pools(
    yield_pools: Iterable[dict[str, Any]],
    products: Iterable[ProductSpec],
    snapshot_date: dt.date,
) -> list[NormalizedSnapshot]:
    """Attach APY to products whose symbol matches a DefiLlama yield pool.

    Matching is by exact symbol because fuzzy matching here is actively dangerous:
    pairing a Treasury fund with an unrelated high-yield pool would put a fake
    double-digit APY on the comparison chart. Anything that doesn't match exactly
    is left with no APY, and the pydantic plausibility bound catches the rest.
    """
    pools_by_symbol: dict[str, dict[str, Any]] = {}
    for pool in yield_pools:
        pool_symbol = (pool.get("symbol") or "").strip().upper()
        if not pool_symbol:
            continue
        # Keep the deepest pool per symbol; thin pools carry noisy APYs.
        incumbent = pools_by_symbol.get(pool_symbol)
        if incumbent is None or (pool.get("tvlUsd") or 0) > (incumbent.get("tvlUsd") or 0):
            pools_by_symbol[pool_symbol] = pool

    snapshots: list[NormalizedSnapshot] = []
    for product in products:
        pool = pools_by_symbol.get(product.symbol.upper())
        if pool is None:
            continue

        apy_7day = pool.get("apy")
        if apy_7day is None:
            apy_7day = pool.get("apyBase")
        apy_30day = pool.get("apyMean30d")
        if apy_7day is None and apy_30day is None:
            continue

        try:
            snapshots.append(
                NormalizedSnapshot(
                    product_symbol=product.symbol,
                    chain_name=None,
                    snapshot_date=snapshot_date,
                    granularity=Granularity.PRODUCT_TOTAL,
                    apy_7day=apy_7day,
                    apy_30day=apy_30day,
                    source_name=f"{SOURCE_NAME}_yields",
                )
            )
        except ValueError as validation_error:
            # An out-of-range APY means we matched the wrong pool. Drop the row and
            # say so, rather than letting it reach the chart.
            logger.warning(
                "Rejected implausible APY for %s from pool %s: %s",
                product.symbol,
                pool.get("pool"),
                validation_error,
            )

    return snapshots
