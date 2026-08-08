"""Normalized data contract shared by every ingestion source.

Every source module converts its provider-specific payload into a
`NormalizedSnapshot` and nothing else. That is the whole point of this file: the
pipeline and the database never see a DefiLlama-shaped or issuer-shaped dict, so
adding a source can't quietly widen the schema.
"""

from __future__ import annotations

import datetime as dt
import enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class NavModel(str, enum.Enum):
    """How a product's per-token value behaves, which decides whether token
    supply can be converted to USD at all."""

    # Token is pinned to $1.00 and yield is distributed as additional tokens, so
    # supply * $1 is a legitimate TVL figure.
    STABLE_ONE_DOLLAR = "stable_one_dollar"

    # NAV rises as interest accrues, so supply * $1 understates TVL by the whole
    # accrued yield. TVL must come from a source that reports NAV or AUM.
    ACCRUING = "accruing"


class Granularity(str, enum.Enum):
    """Whether a snapshot describes one chain or a product's whole footprint.

    Mixing these in an aggregate is the one bug that would silently inflate every
    headline number, so it is an explicit part of the contract rather than a
    convention.
    """

    PER_CHAIN = "per_chain"
    PRODUCT_TOTAL = "product_total"


class ProductDeploymentSpec(BaseModel):
    """One product's presence on one chain, as declared in config/products.json."""

    chain_name: str
    contract_address: Optional[str] = None
    token_decimals: Optional[int] = None
    address_verified: bool = False

    @field_validator("contract_address")
    @classmethod
    def normalize_evm_address(cls, raw_address: Optional[str]) -> Optional[str]:
        # Addresses are compared and deduplicated as strings throughout, so they
        # are lowercased once here rather than at every call site.
        if raw_address is None:
            return None
        stripped = raw_address.strip()
        return stripped.lower() if stripped else None


class ProductSpec(BaseModel):
    """One tracked product, as declared in config/products.json."""

    symbol: str
    display_name: str
    issuer_name: str
    platform_name: Optional[str] = None
    legal_wrapper: Optional[str] = None
    underlying_asset: str
    inception_date: Optional[dt.date] = None
    is_accredited_only: bool = False
    homepage_url: Optional[str] = None
    nav_model: NavModel
    defillama_slug: Optional[str] = None
    slug_verified: bool = False

    # DefiLlama reports TVL per *protocol*, not per product, so several of our
    # products can legitimately share one slug (ondo-yield-assets covers both OUSG
    # and USDY). Exactly one product in a shared-slug group may book that TVL;
    # the others set this False and contribute APY only. Without it, a shared slug
    # would be counted once per product and inflate that issuer's share.
    record_tvl_from_slug: bool = True

    deployments: list[ProductDeploymentSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def reject_duplicate_chains(self) -> "ProductSpec":
        chain_names = [deployment.chain_name for deployment in self.deployments]
        duplicates = {name for name in chain_names if chain_names.count(name) > 1}
        if duplicates:
            raise ValueError(
                f"{self.symbol} lists the same chain twice: {sorted(duplicates)}. "
                "One row per product per chain, or the snapshot unique constraint "
                "will reject the ingest."
            )
        return self


class ChainSpec(BaseModel):
    """A chain we can attribute a deployment to."""

    chain_name: str
    evm_chain_id: Optional[int] = None
    explorer_url: Optional[str] = None
    rpc_env_var: Optional[str] = None
    fallback_public_rpc: Optional[str] = None

    @property
    def is_evm(self) -> bool:
        # Only EVM chains are readable by the web3.py source in v1; non-EVM
        # deployments fall back to aggregator data.
        return self.evm_chain_id is not None


class ProductRegistry(BaseModel):
    """Full parsed contents of config/products.json."""

    chains: list[ChainSpec]
    products: list[ProductSpec]

    @model_validator(mode="after")
    def reject_deployments_on_unknown_chains(self) -> "ProductRegistry":
        known_chain_names = {chain.chain_name for chain in self.chains}
        for product in self.products:
            for deployment in product.deployments:
                if deployment.chain_name not in known_chain_names:
                    raise ValueError(
                        f"{product.symbol} is deployed on unknown chain "
                        f"'{deployment.chain_name}'. Add it to the chains list."
                    )
        return self

    @model_validator(mode="after")
    def reject_double_counted_shared_slugs(self) -> "ProductRegistry":
        """Fail loudly when two products would both book the same protocol's TVL.

        This is the config mistake with the worst consequences: it produces a
        confident, plausible, wrong number rather than an error.
        """
        owners_by_slug: dict[str, list[str]] = {}
        for product in self.products:
            if product.defillama_slug and product.record_tvl_from_slug:
                owners_by_slug.setdefault(product.defillama_slug, []).append(product.symbol)

        for slug, owner_symbols in owners_by_slug.items():
            if len(owner_symbols) > 1:
                raise ValueError(
                    f"DefiLlama slug '{slug}' is claimed by {sorted(owner_symbols)}, "
                    "which would count its TVL once per product. Set "
                    "record_tvl_from_slug:false on all but one of them."
                )
        return self

    def chain_by_name(self, chain_name: str) -> ChainSpec:
        for chain in self.chains:
            if chain.chain_name == chain_name:
                return chain
        raise KeyError(f"No chain named '{chain_name}' in the registry")


class NormalizedSnapshot(BaseModel):
    """One measurement of one product, on one chain (or in total), on one day.

    Every numeric field is optional because no single source populates all of
    them. A source reports what it actually knows and leaves the rest None; the
    dashboard then shows gaps as gaps instead of as zeros.
    """

    product_symbol: str
    chain_name: Optional[str] = None
    snapshot_date: dt.date
    granularity: Granularity

    total_supply_raw: Optional[int] = None
    tvl_usd: Optional[float] = None
    nav_per_token: Optional[float] = None
    holder_count: Optional[int] = None
    apy_7day: Optional[float] = None
    apy_30day: Optional[float] = None

    source_name: str

    @model_validator(mode="after")
    def chain_presence_must_match_granularity(self) -> "NormalizedSnapshot":
        if self.granularity is Granularity.PER_CHAIN and self.chain_name is None:
            raise ValueError(
                f"{self.product_symbol}: per_chain snapshot has no chain_name"
            )
        if self.granularity is Granularity.PRODUCT_TOTAL and self.chain_name is not None:
            raise ValueError(
                f"{self.product_symbol}: product_total snapshot must not name a "
                f"chain, got '{self.chain_name}'"
            )
        return self

    @field_validator("tvl_usd", "nav_per_token")
    @classmethod
    def reject_negative_usd_values(cls, value: Optional[float]) -> Optional[float]:
        if value is not None and value < 0:
            raise ValueError(f"USD value cannot be negative, got {value}")
        return value

    @field_validator("apy_7day", "apy_30day")
    @classmethod
    def reject_implausible_apy(cls, value: Optional[float]) -> Optional[float]:
        # Treasury yields sit in the low single digits. A double-digit "APY" here
        # almost always means the provider expressed a ratio we misread, or we
        # matched the wrong pool, and it would poison the yield comparison chart.
        if value is not None and not (-5.0 <= value <= 25.0):
            raise ValueError(
                f"APY {value} is outside the plausible range for a Treasury "
                "product; check the source's units (percent vs fraction)"
            )
        return value
