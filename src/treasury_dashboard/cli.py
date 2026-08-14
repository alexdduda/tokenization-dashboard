"""Command line entry point for the ingestion layer.

Stage 1 of the build is "get ingestion working and verified", so every command here
prints what it actually did — rows written, products skipped and why, and the
resulting market total — rather than exiting silently on success.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import httpx

from .database import DEFAULT_DATABASE_PATH, IngestionRun, build_engine, open_session
from .export import DEFAULT_EXPORT_PATH, DEFAULT_SERIES_STRIDE_DAYS, write_dashboard_json
from .pipeline import (
    IngestResult,
    run_address_verification,
    run_defillama_ingest,
    run_onchain_ingest,
)
from .queries import latest_snapshot_date, market_size_by_date, product_table
from .registry import (
    DEFAULT_REGISTRY_PATH,
    load_raw_registry,
    load_registry,
    set_deployment_address,
    sync_registry_to_database,
    write_raw_registry,
)
from .sources import defillama
from .sources.onchain import AddressVerdict


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-8s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _format_usd(amount: float | None) -> str:
    if amount is None:
        return "n/a"
    if abs(amount) >= 1e9:
        return f"${amount / 1e9:,.2f}B"
    if abs(amount) >= 1e6:
        return f"${amount / 1e6:,.1f}M"
    return f"${amount:,.0f}"


def _print_ingest_result(result: IngestResult) -> None:
    print(f"\nsource        : {result.source_name}")
    print(f"status        : {result.run_status}")
    print(f"rows written  : {result.rows_written}")
    print(f"products with data: {result.products_covered}")
    if result.products_skipped:
        print("skipped:")
        for skipped in result.products_skipped:
            print(f"  - {skipped}")
    if result.error_messages:
        print("errors:")
        for message in result.error_messages:
            print(f"  - {message}")


def command_init_db(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry)
    engine = build_engine(args.database)
    with open_session(engine) as session:
        counts = sync_registry_to_database(session, registry)
    print(
        f"Initialised {args.database} with {counts['products']} products, "
        f"{counts['deployments']} deployments across {counts['chains']} chains."
    )

    unverified = [
        f"{product.symbol}/{deployment.chain_name}"
        for product in registry.products
        for deployment in product.deployments
        if deployment.contract_address and not deployment.address_verified
    ]
    missing_slugs = [
        product.symbol for product in registry.products if not product.defillama_slug
    ]
    if missing_slugs:
        print(
            f"\nNo DefiLlama slug yet for: {', '.join(missing_slugs)}\n"
            "  Run: treasury-dashboard discover-slugs"
        )
    if unverified:
        print(f"\nUnverified contract addresses (will be skipped): {', '.join(unverified)}")
    return 0


def command_discover_slugs(args: argparse.Namespace) -> int:
    """List RWA-category protocols from live DefiLlama data.

    Exists because guessing slugs offline produces silent mismatches — a wrong slug
    returns a valid-looking payload for the wrong protocol. Copy the slugs for the
    tracked products into config/products.json and set slug_verified:true.
    """
    try:
        with httpx.Client(follow_redirects=True) as http_client:
            all_protocols = defillama.fetch_all_protocols(http_client)
    except httpx.HTTPError as http_error:
        # A raw traceback here tells the operator nothing actionable; the two cases
        # that actually happen are "no egress" and "DefiLlama is having a moment".
        print(
            f"Could not reach DefiLlama ({type(http_error).__name__}: {http_error}).\n"
            f"  Check that {defillama.PROTOCOLS_URL} is reachable from this machine — "
            "a sandbox or corporate proxy that blocks outbound HTTPS produces exactly "
            "this error. Retry if it looks transient.",
            file=sys.stderr,
        )
        return 1

    candidates = defillama.find_rwa_protocol_candidates(all_protocols)
    print(f"{len(candidates)} RWA-category protocols on DefiLlama, largest first:\n")
    print(f"{'slug':<40} {'name':<34} {'tvl':>12}  chains")
    print("-" * 110)
    for protocol in candidates[: args.limit]:
        chains = ", ".join((protocol.get("chains") or [])[:5])
        print(
            f"{protocol.get('slug', ''):<40} {protocol.get('name', ''):<34} "
            f"{_format_usd(protocol.get('tvl')):>12}  {chains}"
        )
    return 0


def command_ingest(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry)
    engine = build_engine(args.database)

    exit_code = 0
    with open_session(engine) as session:
        # Re-synced on every ingest so config edits take effect without a migration.
        sync_registry_to_database(session, registry)

        if args.source in ("defillama", "all"):
            with httpx.Client(follow_redirects=True) as http_client:
                result = run_defillama_ingest(
                    session, registry, http_client, history_days=args.history_days
                )
            _print_ingest_result(result)
            if result.run_status == "failed":
                exit_code = 1

        if args.source in ("onchain", "all"):
            result = run_onchain_ingest(
                session, registry, allow_unverified_addresses=args.allow_unverified
            )
            _print_ingest_result(result)
            if result.run_status == "failed":
                exit_code = 1

        as_of = latest_snapshot_date(session, source_name="defillama")
        if as_of is not None:
            series = market_size_by_date(session)
            if series:
                print(
                    f"\nMarket total on {as_of}: {_format_usd(series[-1][1])} "
                    f"across {len(series)} days of history"
                )
    return exit_code


def command_status(args: argparse.Namespace) -> int:
    engine = build_engine(args.database)
    with open_session(engine) as session:
        as_of = latest_snapshot_date(session)
        if as_of is None:
            print("No snapshots yet. Run: treasury-dashboard ingest")
            return 0

        series = market_size_by_date(session)
        print(f"Latest snapshot date : {as_of}")
        print(f"History depth        : {len(series)} days")
        if series:
            print(f"Latest market total  : {_format_usd(series[-1][1])}")
            # Sanity band from the proposal: totals far outside this mean the
            # pipeline is wrong, not the market.
            if not (5e9 <= series[-1][1] <= 40e9):
                print(
                    "  WARNING: total is outside the $5B-$40B plausibility band for "
                    "this market. Check for double counting or a wrong slug."
                )

        print("\nProducts:")
        print(f"{'symbol':<8} {'issuer':<20} {'tvl':>10} {'apy7d':>7} {'chains':>7}")
        print("-" * 58)
        for row in product_table(session, as_of):
            apy = f"{row['apy_7day']:.2f}%" if row["apy_7day"] is not None else "n/a"
            print(
                f"{row['symbol']:<8} {row['issuer_name']:<20} "
                f"{_format_usd(row['tvl_usd']):>10} {apy:>7} {row['chain_count']:>7}"
            )

        print("\nRecent ingestion runs:")
        recent_runs = (
            session.query(IngestionRun)
            .order_by(IngestionRun.run_id.desc())
            .limit(args.runs)
            .all()
        )
        for run in recent_runs:
            print(
                f"  #{run.run_id} {run.source_name:<18} {run.run_status:<10} "
                f"{run.rows_written:>6} rows  {run.started_at:%Y-%m-%d %H:%M}"
                + (f"  {run.error_message}" if run.error_message else "")
            )
    return 0


def command_discover_addresses(args: argparse.Namespace) -> int:
    """Pull candidate token addresses from DefiLlama into config/products.json.

    Candidates only — they land with address_verified:false and the on-chain reader
    keeps ignoring them until `verify-addresses` (or you) says otherwise.
    """
    registry = load_registry(args.registry)
    raw_config = load_raw_registry(args.registry)

    # A slug shared by several products (ondo-yield-assets covers OUSG and USDY) yields
    # ONE primary-token address that cannot be attributed to either. Assigning it to
    # both would put the same contract on two products, which is never right.
    products_by_slug: dict[str, list[str]] = {}
    for product in registry.products:
        if product.defillama_slug:
            products_by_slug.setdefault(product.defillama_slug, []).append(product.symbol)

    found: list[tuple[str, str, str]] = []
    ambiguous: list[tuple[str, str, str, list[str]]] = []
    try:
        with httpx.Client(follow_redirects=True) as http_client:
            # Fetch each slug once, not once per product sharing it.
            for slug, sharing_symbols in products_by_slug.items():
                detail = defillama.fetch_protocol_detail(http_client, slug)
                for chain_name, address in defillama.extract_address_candidates(detail):
                    if len(sharing_symbols) > 1:
                        ambiguous.append((slug, chain_name, address, sharing_symbols))
                    else:
                        found.append((sharing_symbols[0], chain_name, address))
    except httpx.HTTPError as http_error:
        print(f"Could not reach DefiLlama: {http_error}", file=sys.stderr)
        return 1

    changed_count = 0
    for symbol, chain_name, address in found:
        if set_deployment_address(raw_config, symbol, chain_name, contract_address=address):
            changed_count += 1

    if changed_count:
        write_raw_registry(raw_config, args.registry)

    print(f"Found {len(found)} candidate addresses; {changed_count} new or changed.\n")
    if found:
        print("Confirm each against the issuer's own documentation, then either run")
        print("`treasury-dashboard verify-addresses` or set address_verified:true by hand.\n")
        chains_by_name = {chain.chain_name: chain for chain in registry.chains}
        for symbol, chain_name, address in found:
            explorer = chains_by_name[chain_name].explorer_url
            link = f"{explorer}/address/{address}" if explorer else address
            print(f"  {symbol:<12} {chain_name:<10} {link}")

    if ambiguous:
        print(
            f"\nNot written — {len(ambiguous)} address(es) belong to a slug shared by "
            "several products, and DefiLlama reports only one primary token, so which "
            "product owns it cannot be inferred. Assign by hand after checking the "
            "explorer:"
        )
        chains_by_name = {chain.chain_name: chain for chain in registry.chains}
        for slug, chain_name, address, sharing_symbols in ambiguous:
            explorer = chains_by_name[chain_name].explorer_url
            link = f"{explorer}/address/{address}" if explorer else address
            print(f"  {slug} ({', '.join(sharing_symbols)}) on {chain_name}: {link}")

    unresolved = [
        f"{product.symbol}/{deployment.chain_name}"
        for product in registry.products
        for deployment in product.deployments
        if deployment.contract_address is None
    ]
    if unresolved:
        print(
            f"\nStill without an address ({len(unresolved)}). DefiLlama only reports a "
            "protocol's primary token, so the rest need the issuer's docs:\n  "
            + ", ".join(unresolved)
        )
    return 0


def command_verify_addresses(args: argparse.Namespace) -> int:
    """Reconcile each candidate address against the aggregator's reported TVL."""
    registry = load_registry(args.registry)
    engine = build_engine(args.database)

    with open_session(engine) as session:
        try:
            checks = run_address_verification(session, registry)
        except ImportError:
            print(
                "web3 is not installed. Install the optional extra:\n"
                '  pip install -e ".[onchain]"',
                file=sys.stderr,
            )
            return 1

    if not checks:
        print("No candidate addresses to check. Run: treasury-dashboard discover-addresses")
        return 0

    print(f"{'product':<12} {'chain':<10} {'verdict':<12} detail")
    print("-" * 100)
    for check in checks:
        print(
            f"{check.product_symbol:<12} {check.chain_name:<10} "
            f"{check.verdict.value:<12} {check.detail}"
        )

    confirmed = [c for c in checks if c.verdict is AddressVerdict.CONFIRMED]
    mismatched = [c for c in checks if c.verdict is AddressVerdict.MISMATCH]

    if confirmed and args.promote:
        raw_config = load_raw_registry(args.registry)
        for check in confirmed:
            set_deployment_address(
                raw_config,
                check.product_symbol,
                check.chain_name,
                address_verified=True,
            )
            if check.token_decimals is not None:
                # Trust the contract's own decimals over whatever config guessed.
                for product in raw_config["products"]:
                    if product["symbol"] != check.product_symbol:
                        continue
                    for deployment in product["deployments"]:
                        if deployment["chain_name"] == check.chain_name:
                            deployment["token_decimals"] = check.token_decimals
        write_raw_registry(raw_config, args.registry)
        print(f"\nPromoted {len(confirmed)} address(es) to verified.")
    elif confirmed:
        print(
            f"\n{len(confirmed)} address(es) reconcile exactly. Re-run with --promote to "
            "mark them verified."
        )

    unreadable = [c for c in checks if c.verdict is AddressVerdict.UNREADABLE]
    if unreadable:
        # Separated from mismatches on purpose: this is our connectivity failing, not
        # evidence about the address.
        print(
            f"\n{len(unreadable)} address(es) could not be READ — a network or RPC "
            "problem, which says nothing about whether they are correct. Retry, or set "
            "the chain's RPC env var to a provider you trust."
        )

    if mismatched:
        print(
            f"\n{len(mismatched)} address(es) DISAGREE with reported TVL and are almost "
            "certainly the wrong contract. These are never promoted automatically."
        )
        return 1
    return 0


def command_export(args: argparse.Namespace) -> int:
    """Write the static JSON the frontend reads."""
    engine = build_engine(args.database)
    with open_session(engine) as session:
        try:
            export_path, byte_size = write_dashboard_json(
                session, export_path=args.out, series_stride_days=args.stride_days
            )
        except ValueError as export_error:
            print(f"Nothing to export: {export_error}", file=sys.stderr)
            return 1

    print(f"Wrote {export_path} ({byte_size / 1024:.1f} KiB)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="treasury-dashboard",
        description="Ingest and inspect tokenized US Treasury data.",
    )
    parser.add_argument("--verbose", action="store_true", help="debug-level logging")
    parser.add_argument(
        "--database", type=Path, default=DEFAULT_DATABASE_PATH, help="SQLite file path"
    )
    parser.add_argument(
        "--registry", type=Path, default=DEFAULT_REGISTRY_PATH, help="product registry JSON"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "init-db", help="create the database and load the product registry"
    ).set_defaults(handler=command_init_db)

    discover_parser = subparsers.add_parser(
        "discover-slugs", help="list DefiLlama RWA protocols so slugs can be filled in"
    )
    discover_parser.add_argument("--limit", type=int, default=40)
    discover_parser.set_defaults(handler=command_discover_slugs)

    ingest_parser = subparsers.add_parser("ingest", help="run one or all sources")
    ingest_parser.add_argument(
        "--source", choices=["defillama", "onchain", "all"], default="defillama"
    )
    ingest_parser.add_argument(
        "--history-days",
        type=int,
        default=None,
        help="limit backfill depth; omit for full available history",
    )
    ingest_parser.add_argument(
        "--allow-unverified",
        action="store_true",
        help="read contract addresses not yet marked address_verified (unsafe)",
    )
    ingest_parser.set_defaults(handler=command_ingest)

    status_parser = subparsers.add_parser("status", help="what is in the database")
    status_parser.add_argument("--runs", type=int, default=5)
    status_parser.set_defaults(handler=command_status)

    subparsers.add_parser(
        "discover-addresses",
        help="pull candidate token addresses from DefiLlama into the registry",
    ).set_defaults(handler=command_discover_addresses)

    verify_parser = subparsers.add_parser(
        "verify-addresses",
        help="check candidate addresses against reported TVL by reading the chain",
    )
    verify_parser.add_argument(
        "--promote",
        action="store_true",
        help="mark conclusively reconciled addresses as verified",
    )
    verify_parser.set_defaults(handler=command_verify_addresses)

    export_parser = subparsers.add_parser(
        "export", help="write the static JSON the frontend reads"
    )
    export_parser.add_argument("--out", type=Path, default=DEFAULT_EXPORT_PATH)
    export_parser.add_argument(
        "--stride-days",
        type=int,
        default=DEFAULT_SERIES_STRIDE_DAYS,
        help="downsample the history series; 1 keeps every day",
    )
    export_parser.set_defaults(handler=command_export)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(args.verbose)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
