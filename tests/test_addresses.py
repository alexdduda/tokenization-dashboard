"""Address discovery, registry editing, and verification-by-reconciliation.

The reconciliation logic is the interesting part: it is what lets an address be trusted
without a human opening a block explorer, so its failure cases matter more than its
happy path.
"""

import json

import pytest

from treasury_dashboard.models import NavModel
from treasury_dashboard.registry import (
    load_raw_registry,
    set_deployment_address,
    write_raw_registry,
)
from treasury_dashboard.sources.defillama import extract_address_candidates
from treasury_dashboard.sources.onchain import (
    AddressVerdict,
    reconcile_supply_with_reported_tvl,
)

ONE_MILLION_AT_6_DECIMALS = 1_000_000_000_000


# ---------- candidate extraction ----------


def test_chain_prefixed_address_is_attributed_to_that_chain():
    detail = {"address": "polygon:0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "chains": ["Ethereum"]}

    assert extract_address_candidates(detail) == [
        ("Polygon", "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    ]


def test_bare_address_falls_back_to_the_first_listed_chain():
    detail = {"address": "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB", "chains": ["ethereum", "base"]}

    assert extract_address_candidates(detail) == [
        ("Ethereum", "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
    ]


@pytest.mark.parametrize(
    "detail",
    [
        {"address": "-", "chains": ["ethereum"]},
        {"address": "", "chains": ["ethereum"]},
        {"chains": ["ethereum"]},
        # Non-EVM: the reader cannot use these, so returning them would be noise.
        {"address": "solana:So11111111111111111111111111111111111111112", "chains": ["solana"]},
        # Malformed hex must never reach the registry.
        {"address": "0x1234", "chains": ["ethereum"]},
    ],
)
def test_unusable_addresses_are_dropped(detail):
    assert extract_address_candidates(detail) == []


# ---------- registry editing ----------


def test_setting_an_address_preserves_annotation_keys(tmp_path):
    """The `_slug_note` blocks record why the config makes the calls it does. Round-
    tripping through the pydantic model would drop them, so the raw form is edited."""
    config_path = tmp_path / "products.json"
    config_path.write_text(
        json.dumps(
            {
                "_provenance": {"purpose": "keep me"},
                "chains": [{"chain_name": "Ethereum", "evm_chain_id": 1}],
                "products": [
                    {
                        "symbol": "TESTFUND",
                        "_slug_note": "keep me too",
                        "deployments": [
                            {"chain_name": "Ethereum", "contract_address": None,
                             "address_verified": False}
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    raw = load_raw_registry(config_path)
    assert set_deployment_address(raw, "TESTFUND", "Ethereum", contract_address="0xABC") is True
    write_raw_registry(raw, config_path)

    reloaded = json.loads(config_path.read_text(encoding="utf-8"))
    assert reloaded["_provenance"]["purpose"] == "keep me"
    assert reloaded["products"][0]["_slug_note"] == "keep me too"
    assert reloaded["products"][0]["deployments"][0]["contract_address"] == "0xabc"


def test_changing_an_address_resets_its_verified_flag():
    """A verified flag attests to one specific address. Swapping the address without
    clearing it would let an unchecked contract inherit the trust."""
    raw = {
        "products": [
            {
                "symbol": "TESTFUND",
                "deployments": [
                    {"chain_name": "Ethereum", "contract_address": "0xold",
                     "address_verified": True}
                ],
            }
        ]
    }

    set_deployment_address(raw, "TESTFUND", "Ethereum", contract_address="0xNEW")

    deployment = raw["products"][0]["deployments"][0]
    assert deployment["contract_address"] == "0xnew"
    assert deployment["address_verified"] is False


def test_unchanged_address_reports_no_change():
    raw = {
        "products": [
            {
                "symbol": "TESTFUND",
                "deployments": [
                    {"chain_name": "Ethereum", "contract_address": "0xabc",
                     "address_verified": True}
                ],
            }
        ]
    }

    assert set_deployment_address(raw, "TESTFUND", "Ethereum", contract_address="0xABC") is False
    assert raw["products"][0]["deployments"][0]["address_verified"] is True


def test_deployment_is_created_for_a_chain_not_yet_listed():
    raw = {"products": [{"symbol": "TESTFUND", "deployments": []}]}

    assert set_deployment_address(raw, "TESTFUND", "Base", contract_address="0xabc") is True
    assert raw["products"][0]["deployments"][0]["chain_name"] == "Base"


def test_unknown_symbol_raises():
    with pytest.raises(KeyError, match="NOPE"):
        set_deployment_address({"products": []}, "NOPE", "Ethereum", contract_address="0xabc")


# ---------- reconciliation ----------


def test_stable_nav_supply_matching_reported_tvl_is_confirmed():
    verdict, detail = reconcile_supply_with_reported_tvl(
        ONE_MILLION_AT_6_DECIMALS, 6, NavModel.STABLE_ONE_DOLLAR, 1_000_000.0
    )

    assert verdict is AddressVerdict.CONFIRMED
    assert "ratio 1.000" in detail


def test_stable_nav_tolerates_snapshot_timing_drift():
    """The aggregator figure can be hours older than the chain read."""
    verdict, _ = reconcile_supply_with_reported_tvl(
        ONE_MILLION_AT_6_DECIMALS, 6, NavModel.STABLE_ONE_DOLLAR, 1_080_000.0
    )

    assert verdict is AddressVerdict.CONFIRMED


def test_wrong_contract_is_caught_as_a_mismatch():
    """Reading some unrelated token yields a supply nowhere near the reported TVL —
    which is exactly the signal that makes this check worth doing."""
    verdict, detail = reconcile_supply_with_reported_tvl(
        ONE_MILLION_AT_6_DECIMALS, 6, NavModel.STABLE_ONE_DOLLAR, 3_500_000_000.0
    )

    assert verdict is AddressVerdict.MISMATCH
    assert "aggregator reports" in detail


def test_accruing_nav_is_plausible_but_never_confirmed():
    """An accruing token is worth more than $1 by an unknown accrued amount, so it can
    be shown consistent but not pinned. It stays a human decision."""
    verdict, detail = reconcile_supply_with_reported_tvl(
        ONE_MILLION_AT_6_DECIMALS, 6, NavModel.ACCRUING, 1_090_000.0
    )

    assert verdict is AddressVerdict.PLAUSIBLE
    assert "accrued yield" in detail


def test_accruing_nav_rejects_a_ratio_yield_cannot_explain():
    verdict, _ = reconcile_supply_with_reported_tvl(
        ONE_MILLION_AT_6_DECIMALS, 6, NavModel.ACCRUING, 9_000_000.0
    )

    assert verdict is AddressVerdict.MISMATCH


def test_missing_reference_is_not_treated_as_a_pass():
    verdict, _ = reconcile_supply_with_reported_tvl(
        ONE_MILLION_AT_6_DECIMALS, 6, NavModel.STABLE_ONE_DOLLAR, None
    )

    assert verdict is AddressVerdict.NO_REFERENCE


def test_zero_supply_is_a_mismatch_not_a_division_error():
    verdict, detail = reconcile_supply_with_reported_tvl(
        0, 6, NavModel.STABLE_ONE_DOLLAR, 1_000_000.0
    )

    assert verdict is AddressVerdict.MISMATCH
    assert "zero supply" in detail


def test_unreadable_is_distinct_from_mismatch():
    """A network failure must never be reported as evidence the address is wrong.
    Conflating them produced a confident false negative in a real run."""
    assert AddressVerdict.UNREADABLE != AddressVerdict.MISMATCH
    assert AddressVerdict.UNREADABLE.value == "unreadable"
