"""Load config/products.json and sync it into the database dimensions."""

from __future__ import annotations

import json
from pathlib import Path

from .database import Chain, Product, ProductDeployment
from .models import ProductRegistry

DEFAULT_REGISTRY_PATH = Path("config/products.json")


def load_registry(registry_path: Path = DEFAULT_REGISTRY_PATH) -> ProductRegistry:
    """Parse and validate the registry file.

    Validation happens here rather than at first use so a typo in the config fails
    immediately with a clear message instead of surfacing as a missing FK three
    stages into an ingest.
    """
    raw_config = json.loads(registry_path.read_text(encoding="utf-8"))
    # The leading _provenance block is documentation for whoever edits the file.
    raw_config.pop("_provenance", None)
    return ProductRegistry.model_validate(raw_config)


def sync_registry_to_database(session, registry: ProductRegistry) -> dict[str, int]:
    """Upsert chains, products and deployments; return a summary count.

    Idempotent: safe to run before every ingest, which is how the pipeline picks up
    config edits without a migration step.
    """
    chain_ids_by_name: dict[str, int] = {}

    for chain_spec in registry.chains:
        chain_row = (
            session.query(Chain).filter(Chain.chain_name == chain_spec.chain_name).one_or_none()
        )
        if chain_row is None:
            chain_row = Chain(chain_name=chain_spec.chain_name)
            session.add(chain_row)
        chain_row.evm_chain_id = chain_spec.evm_chain_id
        chain_row.explorer_url = chain_spec.explorer_url
        session.flush()
        chain_ids_by_name[chain_spec.chain_name] = chain_row.chain_id

    products_written = 0
    deployments_written = 0

    for product_spec in registry.products:
        product_row = (
            session.query(Product).filter(Product.symbol == product_spec.symbol).one_or_none()
        )
        if product_row is None:
            product_row = Product(symbol=product_spec.symbol)
            session.add(product_row)
        product_row.display_name = product_spec.display_name
        product_row.issuer_name = product_spec.issuer_name
        product_row.platform_name = product_spec.platform_name
        product_row.legal_wrapper = product_spec.legal_wrapper
        product_row.underlying_asset = product_spec.underlying_asset
        product_row.inception_date = product_spec.inception_date
        product_row.is_accredited_only = product_spec.is_accredited_only
        product_row.homepage_url = product_spec.homepage_url
        product_row.nav_model = product_spec.nav_model.value
        product_row.defillama_slug = product_spec.defillama_slug
        session.flush()
        products_written += 1

        for deployment_spec in product_spec.deployments:
            chain_id = chain_ids_by_name[deployment_spec.chain_name]
            deployment_row = (
                session.query(ProductDeployment)
                .filter(
                    ProductDeployment.product_id == product_row.product_id,
                    ProductDeployment.chain_id == chain_id,
                )
                .one_or_none()
            )
            if deployment_row is None:
                deployment_row = ProductDeployment(
                    product_id=product_row.product_id, chain_id=chain_id
                )
                session.add(deployment_row)
            deployment_row.contract_address = deployment_spec.contract_address
            deployment_row.token_decimals = deployment_spec.token_decimals
            deployment_row.address_verified = deployment_spec.address_verified
            deployments_written += 1

    session.commit()
    return {
        "chains": len(chain_ids_by_name),
        "products": products_written,
        "deployments": deployments_written,
    }
