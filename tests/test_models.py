"""Contract tests: the invariants that stop bad data reaching the database."""

import datetime as dt

import pytest
from pydantic import ValidationError

from treasury_dashboard.models import (
    Granularity,
    NormalizedSnapshot,
    ProductRegistry,
    ProductSpec,
)


def _snapshot(**overrides) -> NormalizedSnapshot:
    defaults = {
        "product_symbol": "TESTFUND",
        "chain_name": "Ethereum",
        "snapshot_date": dt.date(2026, 5, 3),
        "granularity": Granularity.PER_CHAIN,
        "source_name": "defillama",
    }
    return NormalizedSnapshot(**{**defaults, **overrides})


def test_per_chain_snapshot_requires_a_chain():
    with pytest.raises(ValidationError, match="no chain_name"):
        _snapshot(chain_name=None)


def test_product_total_snapshot_must_not_name_a_chain():
    """Otherwise a total could be mistaken for a chain row and double counted."""
    with pytest.raises(ValidationError, match="must not name a"):
        _snapshot(granularity=Granularity.PRODUCT_TOTAL, chain_name="Ethereum")


def test_negative_tvl_is_rejected():
    with pytest.raises(ValidationError, match="cannot be negative"):
        _snapshot(tvl_usd=-1.0)


@pytest.mark.parametrize("implausible_apy", [812.4, 26.0, -6.0])
def test_implausible_apy_is_rejected(implausible_apy):
    with pytest.raises(ValidationError, match="plausible range"):
        _snapshot(apy_7day=implausible_apy)


@pytest.mark.parametrize("plausible_apy", [0.0, 3.35, 4.32, 24.9])
def test_plausible_apy_is_accepted(plausible_apy):
    assert _snapshot(apy_7day=plausible_apy).apy_7day == plausible_apy


def test_duplicate_chain_in_a_product_is_rejected():
    with pytest.raises(ValidationError, match="same chain twice"):
        ProductSpec.model_validate(
            {
                "symbol": "DUPE",
                "display_name": "Duplicated Deployment Fund",
                "issuer_name": "Someone",
                "underlying_asset": "US T-bills",
                "nav_model": "stable_one_dollar",
                "deployments": [
                    {"chain_name": "Ethereum"},
                    {"chain_name": "Ethereum"},
                ],
            }
        )


def test_deployment_on_unregistered_chain_is_rejected():
    with pytest.raises(ValidationError, match="unknown chain"):
        ProductRegistry.model_validate(
            {
                "chains": [{"chain_name": "Ethereum", "evm_chain_id": 1}],
                "products": [
                    {
                        "symbol": "GHOST",
                        "display_name": "Ghost Chain Fund",
                        "issuer_name": "Someone",
                        "underlying_asset": "US T-bills",
                        "nav_model": "stable_one_dollar",
                        "deployments": [{"chain_name": "Atlantis"}],
                    }
                ],
            }
        )


def test_contract_addresses_are_lowercased_once():
    spec = ProductSpec.model_validate(
        {
            "symbol": "CASED",
            "display_name": "Mixed Case Address Fund",
            "issuer_name": "Someone",
            "underlying_asset": "US T-bills",
            "nav_model": "stable_one_dollar",
            "deployments": [
                {"chain_name": "Ethereum", "contract_address": "0xAbCdEf0000000000000000000000000000000000"}
            ],
        }
    )
    assert spec.deployments[0].contract_address == (
        "0xabcdef0000000000000000000000000000000000"
    )


def _registry_with_shared_slug(second_product_books_tvl: bool) -> dict:
    def product(symbol: str, records_tvl: bool) -> dict:
        return {
            "symbol": symbol,
            "display_name": f"{symbol} Fund",
            "issuer_name": "Shared Issuer",
            "underlying_asset": "US T-bills",
            "nav_model": "accruing",
            "defillama_slug": "shared-protocol-slug",
            "record_tvl_from_slug": records_tvl,
            "deployments": [{"chain_name": "Ethereum"}],
        }

    return {
        "chains": [{"chain_name": "Ethereum", "evm_chain_id": 1}],
        "products": [product("FIRST", True), product("SECOND", second_product_books_tvl)],
    }


def test_two_products_booking_one_slug_is_rejected():
    """DefiLlama reports TVL per protocol, so OUSG and USDY share one slug. Letting
    both book it would report that issuer at double its real size."""
    with pytest.raises(ValidationError, match="once per product"):
        ProductRegistry.model_validate(_registry_with_shared_slug(True))


def test_shared_slug_with_a_single_owner_is_allowed():
    registry = ProductRegistry.model_validate(_registry_with_shared_slug(False))

    booking_products = [
        product.symbol for product in registry.products if product.record_tvl_from_slug
    ]
    assert booking_products == ["FIRST"]


def test_real_registry_file_is_valid():
    """Guards against a typo in config/products.json reaching a live run."""
    from treasury_dashboard.registry import load_registry

    registry = load_registry()
    tracked_symbols = {product.symbol for product in registry.products}

    # The six from the original brief must always be present; coverage beyond them
    # is expected to grow, so this asserts a superset rather than equality.
    assert {"BUIDL", "USYC", "OUSG", "USDY", "BENJI", "WTGXX"} <= tracked_symbols

    # Every product excluded from the headline total must explain itself, and no two
    # products may book the same slug. Both are enforced by validators; this asserts
    # the shipped config actually satisfies them.
    for product in registry.products:
        if not product.counts_toward_market_total:
            assert product.market_total_exclusion_reason, product.symbol

    tvl_booking_slugs = [
        product.defillama_slug
        for product in registry.products
        if product.defillama_slug and product.record_tvl_from_slug
    ]
    assert len(tvl_booking_slugs) == len(set(tvl_booking_slugs))
