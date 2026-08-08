import datetime as dt
import json
from pathlib import Path

import pytest

from treasury_dashboard.database import build_engine, open_session
from treasury_dashboard.models import ProductRegistry
from treasury_dashboard.registry import sync_registry_to_database

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(filename: str) -> dict:
    return json.loads((FIXTURES_DIR / filename).read_text(encoding="utf-8"))


@pytest.fixture
def protocol_detail() -> dict:
    return load_fixture("defillama_protocol_detail.json")


@pytest.fixture
def yield_pools() -> list[dict]:
    return load_fixture("defillama_yield_pools.json")["data"]


@pytest.fixture
def test_registry() -> ProductRegistry:
    """Two products covering both NAV models, which is the distinction most of the
    normalization logic turns on."""
    return ProductRegistry.model_validate(
        {
            "chains": [
                {
                    "chain_name": "Ethereum",
                    "evm_chain_id": 1,
                    "explorer_url": "https://etherscan.io",
                    "rpc_env_var": "RPC_URL_ETHEREUM",
                    "fallback_public_rpc": "https://eth.llamarpc.com",
                },
                {
                    "chain_name": "Polygon",
                    "evm_chain_id": 137,
                    "rpc_env_var": "RPC_URL_POLYGON",
                    "fallback_public_rpc": "https://polygon.llamarpc.com",
                },
                {"chain_name": "Solana", "evm_chain_id": None},
            ],
            "products": [
                {
                    "symbol": "TESTFUND",
                    "display_name": "Test Treasury Fund",
                    "issuer_name": "Test Asset Manager",
                    "platform_name": "Test Platform",
                    "underlying_asset": "US T-bills",
                    "nav_model": "stable_one_dollar",
                    "defillama_slug": "test-treasury-fund",
                    "deployments": [
                        {
                            "chain_name": "Ethereum",
                            "contract_address": "0x1111111111111111111111111111111111111111",
                            "token_decimals": 6,
                            "address_verified": True,
                        },
                        {
                            "chain_name": "Polygon",
                            "contract_address": "0x2222222222222222222222222222222222222222",
                            "token_decimals": 6,
                            "address_verified": False,
                        },
                        {"chain_name": "Solana", "contract_address": "SoLaNaAddr"},
                    ],
                },
                {
                    "symbol": "ACCRUER",
                    "display_name": "Accruing NAV Fund",
                    "issuer_name": "Other Asset Manager",
                    "underlying_asset": "Overnight repo",
                    "nav_model": "accruing",
                    "defillama_slug": None,
                    "deployments": [
                        {
                            "chain_name": "Ethereum",
                            "contract_address": "0x3333333333333333333333333333333333333333",
                            "token_decimals": 18,
                            "address_verified": True,
                        }
                    ],
                },
            ],
        }
    )


@pytest.fixture
def session(test_registry):
    """In-memory database with the test registry already synced."""
    engine = build_engine(Path(":memory:"))
    with open_session(engine) as open_db_session:
        sync_registry_to_database(open_db_session, test_registry)
        yield open_db_session


@pytest.fixture
def snapshot_date() -> dt.date:
    return dt.date(2026, 5, 3)
