"""Manual source: figures typed in by hand, with provenance and an expiry date.

Some products no free API covers. BENJI is the case that forced this: DefiLlama has
no entry for it, and Franklin Templeton publishes FOBXX's AUM only on its own site.
The alternatives were leaving it permanently blank or quietly inventing a number.

This is the third option — a figure a human copied from the issuer, carrying the URL
it came from and the date it was true. It is deliberately the weakest source in the
project: it never feeds the historical series (there is no history behind it), and it
goes stale on a clock rather than silently ageing forever.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Iterable, Optional

from ..models import Granularity, NormalizedSnapshot, ProductSpec

logger = logging.getLogger(__name__)

SOURCE_NAME = "issuer_manual"

# After this long, a hand-entered figure stops being evidence and starts being
# folklore. Treasury AUM moves fast enough that a two-month-old number can be wrong
# by hundreds of millions.
DEFAULT_MAX_AGE_DAYS = 60


def is_stale(
    as_of: dt.date, today: dt.date, max_age_days: int = DEFAULT_MAX_AGE_DAYS
) -> bool:
    return (today - as_of).days > max_age_days


def age_in_days(as_of: dt.date, today: dt.date) -> int:
    return (today - as_of).days


def build_snapshots(
    products: Iterable[ProductSpec],
    today: dt.date,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> tuple[list[NormalizedSnapshot], list[str]]:
    """Turn manual entries into snapshots; return them plus notes about what was skipped.

    Stale entries are skipped rather than ingested. A number that is visibly missing
    prompts someone to refresh it; a number that is quietly out of date does not.
    """
    snapshots: list[NormalizedSnapshot] = []
    notes: list[str] = []

    for product in products:
        if product.manual_tvl_usd is None:
            continue

        # Validated on the model, but this source is the one place a bad entry would
        # reach the database, so it does not assume.
        as_of: Optional[dt.date] = product.manual_tvl_as_of
        if as_of is None:
            notes.append(f"{product.symbol} (manual figure with no as-of date)")
            continue

        if as_of > today:
            notes.append(f"{product.symbol} (manual as-of date is in the future)")
            continue

        if is_stale(as_of, today, max_age_days):
            notes.append(
                f"{product.symbol} (manual figure is {age_in_days(as_of, today)} days "
                f"old, past the {max_age_days} day limit; refresh it from "
                f"{product.manual_tvl_source_url})"
            )
            continue

        snapshots.append(
            NormalizedSnapshot(
                product_symbol=product.symbol,
                chain_name=None,
                snapshot_date=as_of,
                granularity=Granularity.PRODUCT_TOTAL,
                tvl_usd=product.manual_tvl_usd,
                source_name=SOURCE_NAME,
            )
        )

    return snapshots, notes
