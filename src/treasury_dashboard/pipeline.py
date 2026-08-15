"""Orchestration: run a source, normalize, persist, and record the run.

Persistence is deliberately the only place that knows about database ids. Sources
speak in symbols and chain names; the mapping to ids happens once, here.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Iterable, Optional

import httpx

from .database import Chain, IngestionRun, Product, Snapshot
from .models import NormalizedSnapshot, ProductRegistry
from .sources import defillama, manual, onchain

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    """What a single source run accomplished, for the CLI to print and the run
    table to record."""

    source_name: str
    rows_written: int
    products_covered: int
    products_skipped: list[str]
    error_messages: list[str]

    @property
    def run_status(self) -> str:
        if self.error_messages and self.rows_written == 0:
            return "failed"
        if self.error_messages:
            return "partial"
        return "succeeded"


# The grain of one snapshot row, used to deduplicate before touching the database.
_SnapshotKey = tuple[int, Optional[int], dt.date, str]


def _load_product_ids(session) -> dict[str, int]:
    return {row.symbol: row.product_id for row in session.query(Product).all()}


def _load_or_create_chain_ids(session, chain_names: Iterable[str]) -> dict[str, int]:
    """Map chain names to ids, creating rows for chains we hadn't registered.

    Aggregators legitimately know about deployments our config doesn't list yet, and
    discovering a new chain is a useful signal rather than an error — so the chain
    is created (with no EVM id, making it unreadable on-chain until someone fills
    it in) and logged.
    """
    chain_ids_by_name = {row.chain_name: row.chain_id for row in session.query(Chain).all()}

    for chain_name in chain_names:
        if chain_name in chain_ids_by_name:
            continue
        logger.info(
            "Discovered unregistered chain '%s' from source data; adding it. Add an "
            "evm_chain_id in config/products.json to make it readable on-chain.",
            chain_name,
        )
        new_chain = Chain(chain_name=chain_name)
        session.add(new_chain)
        session.flush()
        chain_ids_by_name[chain_name] = new_chain.chain_id

    return chain_ids_by_name


def persist_snapshots(session, snapshots: list[NormalizedSnapshot]) -> int:
    """Upsert normalized snapshots on their natural grain; return rows written.

    Deduplication happens in Python before the write because SQLite's UNIQUE
    constraint treats NULL chain_ids as distinct, so product-total rows would
    otherwise duplicate on every re-run. Later snapshots in the list win, which
    makes a same-day re-ingest a correction rather than a duplicate.
    """
    if not snapshots:
        return 0

    product_ids_by_symbol = _load_product_ids(session)
    referenced_chain_names = {
        snapshot.chain_name for snapshot in snapshots if snapshot.chain_name is not None
    }
    chain_ids_by_name = _load_or_create_chain_ids(session, referenced_chain_names)

    deduplicated: dict[_SnapshotKey, NormalizedSnapshot] = {}
    unknown_symbols: set[str] = set()

    for snapshot in snapshots:
        product_id = product_ids_by_symbol.get(snapshot.product_symbol)
        if product_id is None:
            unknown_symbols.add(snapshot.product_symbol)
            continue
        chain_id = (
            chain_ids_by_name[snapshot.chain_name] if snapshot.chain_name else None
        )
        key: _SnapshotKey = (
            product_id,
            chain_id,
            snapshot.snapshot_date,
            snapshot.source_name,
        )
        deduplicated[key] = snapshot

    if unknown_symbols:
        logger.warning(
            "Dropped snapshots for symbols absent from the registry: %s",
            sorted(unknown_symbols),
        )

    # One query for every row we might be updating, instead of one query per row.
    dates_touched = {key[2] for key in deduplicated}
    sources_touched = {key[3] for key in deduplicated}
    existing_rows_by_key: dict[_SnapshotKey, Snapshot] = {
        (row.product_id, row.chain_id, row.snapshot_date, row.source_name): row
        for row in session.query(Snapshot)
        .filter(
            Snapshot.snapshot_date.in_(dates_touched),
            Snapshot.source_name.in_(sources_touched),
        )
        .all()
    }

    ingested_at = dt.datetime.now(dt.timezone.utc)
    rows_written = 0

    for key, snapshot in deduplicated.items():
        product_id, chain_id, snapshot_date, source_name = key
        row = existing_rows_by_key.get(key)
        if row is None:
            row = Snapshot(
                product_id=product_id,
                chain_id=chain_id,
                snapshot_date=snapshot_date,
                source_name=source_name,
            )
            session.add(row)

        row.granularity = snapshot.granularity.value
        # uint256 supplies overflow SQLite INTEGER, so supply is stored as text.
        row.total_supply_raw = (
            str(snapshot.total_supply_raw) if snapshot.total_supply_raw is not None else None
        )
        row.tvl_usd = snapshot.tvl_usd
        row.nav_per_token = snapshot.nav_per_token
        row.holder_count = snapshot.holder_count
        row.apy_7day = snapshot.apy_7day
        row.apy_30day = snapshot.apy_30day
        row.ingested_at = ingested_at
        rows_written += 1

    session.commit()
    return rows_written


def _record_run(session, result: IngestResult, started_at: dt.datetime) -> None:
    session.add(
        IngestionRun(
            source_name=result.source_name,
            started_at=started_at,
            finished_at=dt.datetime.now(dt.timezone.utc),
            run_status=result.run_status,
            rows_written=result.rows_written,
            error_message="; ".join(result.error_messages) or None,
        )
    )
    session.commit()


def run_defillama_ingest(
    session,
    registry: ProductRegistry,
    http_client: httpx.Client,
    history_days: Optional[int] = None,
) -> IngestResult:
    """Pull TVL history and yields from DefiLlama for every product with a slug."""
    started_at = dt.datetime.now(dt.timezone.utc)
    today = started_at.date()
    earliest_date = (
        today - dt.timedelta(days=history_days) if history_days is not None else None
    )

    all_snapshots: list[NormalizedSnapshot] = []
    products_covered = 0
    products_skipped: list[str] = []
    error_messages: list[str] = []

    for product in registry.products:
        if not product.defillama_slug:
            # Expected on a fresh clone. `discover-slugs` fills these in.
            products_skipped.append(f"{product.symbol} (no defillama_slug)")
            continue
        if not product.record_tvl_from_slug:
            # Shares its slug with a sibling product that books the TVL; this one
            # still picks up APY from the yields endpoint below.
            products_skipped.append(
                f"{product.symbol} (TVL booked under a sibling sharing slug "
                f"'{product.defillama_slug}')"
            )
            continue
        try:
            detail = defillama.fetch_protocol_detail(http_client, product.defillama_slug)
        except httpx.HTTPError as http_error:
            # One unreachable protocol must not abort the other five.
            error_messages.append(f"{product.symbol}: {http_error}")
            continue

        product_snapshots = defillama.normalize_protocol_history(
            detail, product, earliest_date=earliest_date
        )
        if not product_snapshots:
            products_skipped.append(f"{product.symbol} (no usable TVL points)")
            continue

        all_snapshots.extend(product_snapshots)
        products_covered += 1

    try:
        yield_pools = defillama.fetch_yield_pools(http_client)
        all_snapshots.extend(
            defillama.normalize_yield_pools(yield_pools, registry.products, today)
        )
    except httpx.HTTPError as http_error:
        error_messages.append(f"yields: {http_error}")

    rows_written = persist_snapshots(session, all_snapshots)
    result = IngestResult(
        source_name=defillama.SOURCE_NAME,
        rows_written=rows_written,
        products_covered=products_covered,
        products_skipped=products_skipped,
        error_messages=error_messages,
    )
    _record_run(session, result, started_at)
    return result


def run_manual_ingest(
    session,
    registry: ProductRegistry,
    max_age_days: int = manual.DEFAULT_MAX_AGE_DAYS,
) -> IngestResult:
    """Record hand-entered figures for products no API covers.

    Runs offline — there is nothing to fetch. Kept as a source rather than a special
    case so these figures carry a `source_name` like everything else and the UI can
    show exactly which numbers a human typed.
    """
    started_at = dt.datetime.now(dt.timezone.utc)

    snapshots, skip_notes = manual.build_snapshots(
        registry.products, started_at.date(), max_age_days
    )
    rows_written = persist_snapshots(session, snapshots)

    result = IngestResult(
        source_name=manual.SOURCE_NAME,
        rows_written=rows_written,
        products_covered=len(snapshots),
        products_skipped=skip_notes,
        error_messages=[],
    )
    _record_run(session, result, started_at)
    return result


def latest_reported_tvl(
    session, product_symbol: str, chain_name: str
) -> Optional[float]:
    """The aggregator's most recent TVL for one product on one chain.

    This is the independent reference that address verification checks against.
    """
    from sqlalchemy import select

    from .database import Chain as ChainRow

    product_id = session.execute(
        select(Product.product_id).where(Product.symbol == product_symbol)
    ).scalar()
    chain_id = session.execute(
        select(ChainRow.chain_id).where(ChainRow.chain_name == chain_name)
    ).scalar()
    if product_id is None or chain_id is None:
        return None

    return session.execute(
        select(Snapshot.tvl_usd)
        .where(
            Snapshot.product_id == product_id,
            Snapshot.chain_id == chain_id,
            Snapshot.source_name == defillama.SOURCE_NAME,
            Snapshot.tvl_usd.is_not(None),
        )
        .order_by(Snapshot.snapshot_date.desc())
        .limit(1)
    ).scalar()


@dataclass
class AddressCheck:
    """One candidate address and what checking it against the chain concluded."""

    product_symbol: str
    chain_name: str
    contract_address: str
    verdict: onchain.AddressVerdict
    detail: str
    token_decimals: Optional[int] = None


def run_address_verification(
    session, registry: ProductRegistry
) -> list[AddressCheck]:
    """Read every unverified candidate address and reconcile it against the aggregator.

    Only an exact-enough match on a stable-NAV product is conclusive, so only those get
    promoted to verified automatically. Accruing-NAV products can be shown consistent
    but not proven, and stay a human decision.
    """
    from web3 import Web3

    checks: list[AddressCheck] = []
    web3_clients_by_chain: dict[str, object] = {}

    for product in registry.products:
        for deployment in product.deployments:
            if deployment.contract_address is None:
                continue

            chain = registry.chain_by_name(deployment.chain_name)
            if not chain.is_evm:
                continue

            rpc_url = onchain.resolve_rpc_url(chain)
            if rpc_url is None:
                checks.append(
                    AddressCheck(
                        product.symbol,
                        deployment.chain_name,
                        deployment.contract_address,
                        onchain.AddressVerdict.UNREADABLE,
                        f"no RPC URL; set {chain.rpc_env_var}",
                    )
                )
                continue

            if chain.chain_name not in web3_clients_by_chain:
                web3_clients_by_chain[chain.chain_name] = Web3(Web3.HTTPProvider(rpc_url))

            try:
                total_supply_raw, token_decimals = onchain.read_deployment_supply(
                    web3_clients_by_chain[chain.chain_name],
                    deployment.contract_address,
                    deployment.token_decimals,
                )
            except Exception as read_error:
                # Deliberately NOT a mismatch. Failing to reach an RPC endpoint says
                # nothing about whether the address is correct, and reporting it as a
                # disagreement would be a confident false negative.
                checks.append(
                    AddressCheck(
                        product.symbol,
                        deployment.chain_name,
                        deployment.contract_address,
                        onchain.AddressVerdict.UNREADABLE,
                        f"could not reach the chain: {type(read_error).__name__}. "
                        f"Check {chain.rpc_env_var} or the network, then retry.",
                    )
                )
                continue

            verdict, detail = onchain.reconcile_supply_with_reported_tvl(
                total_supply_raw,
                token_decimals,
                product.nav_model,
                latest_reported_tvl(session, product.symbol, deployment.chain_name),
            )
            checks.append(
                AddressCheck(
                    product.symbol,
                    deployment.chain_name,
                    deployment.contract_address,
                    verdict,
                    detail,
                    token_decimals,
                )
            )

    return checks


def run_onchain_ingest(
    session,
    registry: ProductRegistry,
    allow_unverified_addresses: bool = False,
) -> IngestResult:
    """Read `totalSupply()` for every readable EVM deployment.

    Imported lazily: web3 pulls in a large dependency tree, and the DefiLlama-only
    path should not pay for it.
    """
    from web3 import Web3

    started_at = dt.datetime.now(dt.timezone.utc)
    today = started_at.date()

    all_snapshots: list[NormalizedSnapshot] = []
    products_covered = 0
    products_skipped: list[str] = []
    error_messages: list[str] = []

    # One client per chain, reused across the products deployed on it.
    web3_clients_by_chain: dict[str, object] = {}

    for product in registry.products:
        readable = onchain.readable_deployments(
            product, registry.chain_by_name, allow_unverified_addresses
        )
        if not readable:
            products_skipped.append(f"{product.symbol} (no readable deployment)")
            continue

        product_had_a_reading = False
        for deployment, chain in readable:
            if chain.chain_name not in web3_clients_by_chain:
                rpc_url = onchain.resolve_rpc_url(chain)
                web3_clients_by_chain[chain.chain_name] = Web3(Web3.HTTPProvider(rpc_url))
            web3_client = web3_clients_by_chain[chain.chain_name]

            try:
                total_supply_raw, token_decimals = onchain.read_deployment_supply(
                    web3_client, deployment.contract_address, deployment.token_decimals
                )
            except Exception as read_error:
                error_messages.append(
                    f"{product.symbol} on {chain.chain_name}: {read_error}"
                )
                continue

            all_snapshots.append(
                onchain.build_snapshot_from_supply(
                    product, deployment, total_supply_raw, token_decimals, today
                )
            )
            product_had_a_reading = True

        if product_had_a_reading:
            products_covered += 1

    rows_written = persist_snapshots(session, all_snapshots)
    result = IngestResult(
        source_name=onchain.SOURCE_NAME,
        rows_written=rows_written,
        products_covered=products_covered,
        products_skipped=products_skipped,
        error_messages=error_messages,
    )
    _record_run(session, result, started_at)
    return result
