"""Tests for the on-chain source's pure logic.

No RPC endpoint is contacted: `read_deployment_supply` is the only function that
talks to a chain, and it is kept thin precisely so everything around it is testable.
"""

import datetime as dt

import pytest

from treasury_dashboard.models import Granularity, NavModel
from treasury_dashboard.sources import onchain


def test_stable_nav_supply_converts_to_usd():
    # 1,000,000 tokens at 6 decimals, pinned to $1.00.
    assert onchain.supply_to_tvl_usd(
        1_000_000_000_000, 6, NavModel.STABLE_ONE_DOLLAR
    ) == 1_000_000.0


def test_accruing_nav_supply_does_not_convert_to_usd():
    """For USYC/USDY/OUSG the token is worth more than $1 by the accrued interest, so
    supply * $1 would systematically understate TVL. None is the honest answer."""
    assert onchain.supply_to_tvl_usd(10**24, 18, NavModel.ACCRUING) is None


def test_snapshot_from_supply_carries_nav_only_for_stable_products(
    test_registry, snapshot_date
):
    stable_product = test_registry.products[0]
    accruing_product = test_registry.products[1]

    stable_snapshot = onchain.build_snapshot_from_supply(
        stable_product, stable_product.deployments[0], 5_000_000_000_000, 6, snapshot_date
    )
    accruing_snapshot = onchain.build_snapshot_from_supply(
        accruing_product, accruing_product.deployments[0], 10**24, 18, snapshot_date
    )

    assert stable_snapshot.granularity is Granularity.PER_CHAIN
    assert stable_snapshot.tvl_usd == 5_000_000.0
    assert stable_snapshot.nav_per_token == 1.0
    assert stable_snapshot.total_supply_raw == 5_000_000_000_000

    assert accruing_snapshot.tvl_usd is None
    assert accruing_snapshot.nav_per_token is None
    # Supply is still recorded even though TVL cannot be derived from it.
    assert accruing_snapshot.total_supply_raw == 10**24


def test_unverified_addresses_are_skipped_by_default(test_registry):
    """Reading an unverified address is worse than reading nothing: it yields a
    real-looking number for possibly the wrong token."""
    stable_product = test_registry.products[0]

    readable = onchain.readable_deployments(stable_product, test_registry.chain_by_name)
    assert [deployment.chain_name for deployment, _ in readable] == ["Ethereum"]


def test_unverified_addresses_can_be_opted_into(test_registry):
    stable_product = test_registry.products[0]

    readable = onchain.readable_deployments(
        stable_product, test_registry.chain_by_name, allow_unverified_addresses=True
    )
    # Solana stays excluded regardless: non-EVM is unsupported in v1.
    assert [deployment.chain_name for deployment, _ in readable] == ["Ethereum", "Polygon"]


def test_rpc_env_var_overrides_public_endpoint(test_registry, monkeypatch):
    ethereum = test_registry.chain_by_name("Ethereum")

    assert onchain.resolve_rpc_url(ethereum) == "https://eth.llamarpc.com"

    monkeypatch.setenv("RPC_URL_ETHEREUM", "https://eth-mainnet.g.alchemy.com/v2/key")
    assert onchain.resolve_rpc_url(ethereum) == (
        "https://eth-mainnet.g.alchemy.com/v2/key"
    )


def test_non_evm_chain_has_no_rpc_and_is_not_evm(test_registry):
    solana = test_registry.chain_by_name("Solana")

    assert solana.is_evm is False
    assert onchain.resolve_rpc_url(solana) is None
