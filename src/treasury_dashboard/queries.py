"""Read queries backing the four dashboard views.

Every aggregate here filters on exactly one `granularity`. Summing `per_chain` and
`product_total` rows together would double count each product, which is the single
easiest way to produce a confidently wrong headline number.
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import func, select

from .database import Product, Snapshot
from .models import Granularity


def market_size_by_date(
    session,
    source_name: str = "defillama",
    granularity: Granularity = Granularity.PER_CHAIN,
) -> list[tuple[dt.date, float]]:
    """Total TVL of tracked products per day — the main line chart.

    Excludes products flagged `counts_toward_market_total = False`, which are the ones
    that hold other tracked products. Including OUSG alongside BUIDL and USYC would
    count that ~$2.5B twice, since OUSG is largely invested in them.
    """
    statement = (
        select(Snapshot.snapshot_date, func.sum(Snapshot.tvl_usd))
        .join(Product, Product.product_id == Snapshot.product_id)
        .where(
            Snapshot.source_name == source_name,
            Snapshot.granularity == granularity.value,
            Snapshot.tvl_usd.is_not(None),
            Product.counts_toward_market_total == True,  # noqa: E712 — SQL, not Python
        )
        .group_by(Snapshot.snapshot_date)
        .order_by(Snapshot.snapshot_date)
    )
    return [(row[0], float(row[1])) for row in session.execute(statement).all()]


def latest_snapshot_date(session, source_name: Optional[str] = None) -> Optional[dt.date]:
    statement = select(func.max(Snapshot.snapshot_date))
    if source_name is not None:
        statement = statement.where(Snapshot.source_name == source_name)
    return session.execute(statement).scalar()


def breakdown_by_issuer(
    session,
    as_of_date: dt.date,
    source_name: str = "defillama",
) -> list[tuple[str, float]]:
    """Issuer share of the market on one date — the bar/pie chart.

    Uses the same exclusion as `market_size_by_date` so the slices add up to the
    headline total. Without it, Ondo's slice would include OUSG's holdings of
    BlackRock's and Circle's products and overstate Ondo's share of the market.
    """
    statement = (
        select(Product.issuer_name, func.sum(Snapshot.tvl_usd))
        .join(Product, Product.product_id == Snapshot.product_id)
        .where(
            Snapshot.snapshot_date == as_of_date,
            Snapshot.source_name == source_name,
            Snapshot.granularity == Granularity.PER_CHAIN.value,
            Snapshot.tvl_usd.is_not(None),
            Product.counts_toward_market_total == True,  # noqa: E712 — SQL, not Python
        )
        .group_by(Product.issuer_name)
        .order_by(func.sum(Snapshot.tvl_usd).desc())
    )
    return [(row[0], float(row[1])) for row in session.execute(statement).all()]


def product_table(session, as_of_date: dt.date) -> list[dict]:
    """One row per product with its latest known stats — the sortable table.

    TVL and APY are read from whichever source reported them, because no single
    source covers both: TVL comes from the aggregator or on-chain supply, APY from
    the yields endpoint. Rows with no TVL are still returned so a coverage gap is
    visible in the UI rather than being silently dropped.
    """
    tvl_by_product = dict(
        session.execute(
            select(Snapshot.product_id, func.sum(Snapshot.tvl_usd))
            .where(
                Snapshot.snapshot_date == as_of_date,
                Snapshot.granularity == Granularity.PER_CHAIN.value,
                Snapshot.tvl_usd.is_not(None),
            )
            .group_by(Snapshot.product_id)
        ).all()
    )

    apy_by_product = {
        row[0]: (row[1], row[2])
        for row in session.execute(
            select(Snapshot.product_id, Snapshot.apy_7day, Snapshot.apy_30day)
            .where(
                Snapshot.snapshot_date == as_of_date,
                Snapshot.apy_7day.is_not(None),
            )
        ).all()
    }

    chain_counts = dict(
        session.execute(
            select(Snapshot.product_id, func.count(func.distinct(Snapshot.chain_id)))
            .where(
                Snapshot.snapshot_date == as_of_date,
                Snapshot.granularity == Granularity.PER_CHAIN.value,
            )
            .group_by(Snapshot.product_id)
        ).all()
    )

    rows: list[dict] = []
    for product in session.execute(select(Product).order_by(Product.symbol)).scalars():
        apy_7day, apy_30day = apy_by_product.get(product.product_id, (None, None))
        rows.append(
            {
                "symbol": product.symbol,
                "display_name": product.display_name,
                "issuer_name": product.issuer_name,
                "platform_name": product.platform_name,
                "legal_wrapper": product.legal_wrapper,
                "underlying_asset": product.underlying_asset,
                "nav_model": product.nav_model,
                "is_accredited_only": bool(product.is_accredited_only),
                "inception_date": product.inception_date,
                "tvl_usd": tvl_by_product.get(product.product_id),
                "apy_7day": apy_7day,
                "apy_30day": apy_30day,
                "chain_count": chain_counts.get(product.product_id, 0),
                # Surfaced so the table can show why a product's TVL is present but
                # absent from the headline total.
                "counts_toward_market_total": bool(product.counts_toward_market_total),
                "market_total_exclusion_reason": product.market_total_exclusion_reason,
            }
        )
    return rows
