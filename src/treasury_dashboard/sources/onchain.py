"""On-chain source: read token supply straight from the contract.

This is the ground-truth layer. An aggregator can lag or mis-attribute a chain;
`totalSupply()` cannot. It only covers EVM chains in v1 — the non-EVM deployments
(Solana, Aptos, Sui, Stellar) need per-ecosystem clients and fall back to
aggregator data until then.

Kept keyless by default via public RPC endpoints, with an env var per chain so an
Alchemy or Infura URL can be dropped in when a public endpoint rate-limits.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from typing import Any, Optional

from ..models import (
    ChainSpec,
    Granularity,
    NavModel,
    NormalizedSnapshot,
    ProductDeploymentSpec,
    ProductSpec,
)

logger = logging.getLogger(__name__)

SOURCE_NAME = "onchain_rpc"

# Only the two views we need. A full ERC-20 ABI would be dead weight.
ERC20_MINIMAL_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "totalSupply",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
]


class OnChainReadError(RuntimeError):
    """Raised when a deployment cannot be read, so the pipeline can record a
    partial run instead of dying."""


def resolve_rpc_url(chain: ChainSpec) -> Optional[str]:
    """Prefer an operator-supplied RPC URL, fall back to the public endpoint.

    Public endpoints are fine for six products once a day but throttle hard under
    any real load, which is why the env var takes precedence.
    """
    if chain.rpc_env_var:
        configured_url = os.environ.get(chain.rpc_env_var)
        if configured_url:
            return configured_url
    return chain.fallback_public_rpc


def supply_to_tvl_usd(
    total_supply_raw: int, token_decimals: int, nav_model: NavModel
) -> Optional[float]:
    """Convert raw supply to USD, but only when the NAV model permits it.

    For accruing-NAV products (USYC, USDY, OUSG) the token is worth more than $1 by
    exactly the accrued interest, so `supply * 1.0` would systematically understate
    TVL. Returning None here is the correct answer: the supply is still recorded,
    and TVL is left to a source that reports NAV or AUM directly.
    """
    if nav_model is not NavModel.STABLE_ONE_DOLLAR:
        return None
    return (total_supply_raw / (10**token_decimals)) * 1.0


def read_deployment_supply(
    web3_client: Any,
    contract_address: str,
    declared_decimals: Optional[int],
) -> tuple[int, int]:
    """Return `(total_supply_raw, token_decimals)` for one ERC-20 deployment.

    Decimals are read from the contract rather than trusted from config, because a
    wrong decimals value silently rescales TVL by a factor of 10^n — the kind of
    error that looks like a market event.
    """
    checksum_address = web3_client.to_checksum_address(contract_address)
    contract = web3_client.eth.contract(address=checksum_address, abi=ERC20_MINIMAL_ABI)

    total_supply_raw = int(contract.functions.totalSupply().call())

    try:
        token_decimals = int(contract.functions.decimals().call())
    except Exception:
        if declared_decimals is None:
            raise OnChainReadError(
                f"{contract_address} does not expose decimals() and config declares "
                "none; cannot scale supply"
            )
        logger.warning(
            "decimals() call failed for %s, falling back to configured %d",
            contract_address,
            declared_decimals,
        )
        token_decimals = declared_decimals

    if declared_decimals is not None and token_decimals != declared_decimals:
        logger.warning(
            "%s reports %d decimals but config declares %d; trusting the contract",
            contract_address,
            token_decimals,
            declared_decimals,
        )

    return total_supply_raw, token_decimals


def build_snapshot_from_supply(
    product: ProductSpec,
    deployment: ProductDeploymentSpec,
    total_supply_raw: int,
    token_decimals: int,
    snapshot_date: dt.date,
) -> NormalizedSnapshot:
    """Pure assembly step, split out so it is testable without an RPC endpoint."""
    return NormalizedSnapshot(
        product_symbol=product.symbol,
        chain_name=deployment.chain_name,
        snapshot_date=snapshot_date,
        granularity=Granularity.PER_CHAIN,
        total_supply_raw=total_supply_raw,
        tvl_usd=supply_to_tvl_usd(total_supply_raw, token_decimals, product.nav_model),
        nav_per_token=1.0 if product.nav_model is NavModel.STABLE_ONE_DOLLAR else None,
        source_name=SOURCE_NAME,
    )


def readable_deployments(
    product: ProductSpec,
    registry_chain_lookup,
    allow_unverified_addresses: bool = False,
) -> list[tuple[ProductDeploymentSpec, ChainSpec]]:
    """Filter a product's deployments down to the ones this source can actually read.

    Skips non-EVM chains, deployments with no address, and — unless explicitly
    allowed — addresses nobody has verified yet. Reading an unverified address is
    worse than reading nothing: it produces a real-looking number for possibly the
    wrong token.
    """
    readable: list[tuple[ProductDeploymentSpec, ChainSpec]] = []

    for deployment in product.deployments:
        chain = registry_chain_lookup(deployment.chain_name)

        if not chain.is_evm:
            logger.debug(
                "Skipping %s on %s: non-EVM chains are not supported in v1",
                product.symbol,
                deployment.chain_name,
            )
            continue
        if deployment.contract_address is None:
            logger.info(
                "Skipping %s on %s: no contract address in config/products.json",
                product.symbol,
                deployment.chain_name,
            )
            continue
        if not deployment.address_verified and not allow_unverified_addresses:
            logger.warning(
                "Skipping %s on %s: address %s is not marked verified. Confirm it "
                "against %s, then set address_verified:true.",
                product.symbol,
                deployment.chain_name,
                deployment.contract_address,
                chain.explorer_url or "the chain explorer",
            )
            continue
        if resolve_rpc_url(chain) is None:
            logger.warning(
                "Skipping %s on %s: no RPC URL. Set %s.",
                product.symbol,
                deployment.chain_name,
                chain.rpc_env_var or "an RPC env var",
            )
            continue

        readable.append((deployment, chain))

    return readable
