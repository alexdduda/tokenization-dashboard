"""SQLite schema and session handling.

Mirrors the DDL in docs/data-sources-and-schema.md. If you change one, change the
other.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

DEFAULT_DATABASE_PATH = Path("data/treasuries.sqlite")


class Base(DeclarativeBase):
    pass


class Chain(Base):
    __tablename__ = "chains"

    chain_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chain_name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    # NULL for non-EVM chains (Solana, Aptos, Sui, Stellar).
    evm_chain_id: Mapped[Optional[int]] = mapped_column(Integer)
    explorer_url: Mapped[Optional[str]] = mapped_column(String)


class Product(Base):
    __tablename__ = "products"

    product_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    issuer_name: Mapped[str] = mapped_column(String, nullable=False)
    # Issuer and platform are separate columns because the split between the asset
    # manager and the tokenization platform is the actual structure of this market
    # (BlackRock issues BUIDL; Securitize tokenizes and transfer-agents it).
    platform_name: Mapped[Optional[str]] = mapped_column(String)
    legal_wrapper: Mapped[Optional[str]] = mapped_column(String)
    underlying_asset: Mapped[str] = mapped_column(String, nullable=False)
    inception_date: Mapped[Optional[dt.date]] = mapped_column(Date)
    is_accredited_only: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    homepage_url: Mapped[Optional[str]] = mapped_column(String)
    nav_model: Mapped[str] = mapped_column(String, nullable=False)
    defillama_slug: Mapped[Optional[str]] = mapped_column(String)
    # False for products that hold other tracked products (OUSG holds BUIDL and
    # USYC), which would otherwise be counted twice in the market total.
    counts_toward_market_total: Mapped[bool] = mapped_column(
        Integer, nullable=False, default=1
    )
    market_total_exclusion_reason: Mapped[Optional[str]] = mapped_column(Text)

    deployments: Mapped[list["ProductDeployment"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class ProductDeployment(Base):
    """Makes "BUIDL is live on nine chains" a queryable fact rather than prose."""

    __tablename__ = "product_deployments"
    __table_args__ = (UniqueConstraint("product_id", "chain_id"),)

    deployment_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.product_id"), nullable=False
    )
    chain_id: Mapped[int] = mapped_column(ForeignKey("chains.chain_id"), nullable=False)
    contract_address: Mapped[Optional[str]] = mapped_column(String)
    token_decimals: Mapped[Optional[int]] = mapped_column(Integer)
    # Unverified addresses are still stored so the gap is visible, but the
    # on-chain reader skips them unless explicitly allowed.
    address_verified: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    first_seen_date: Mapped[Optional[dt.date]] = mapped_column(Date)

    product: Mapped[Product] = relationship(back_populates="deployments")


class Snapshot(Base):
    """The fact table: one row per product, per chain (or per product total), per
    day, per source."""

    __tablename__ = "snapshots"
    __table_args__ = (
        # source_name is part of the key so two sources disagreeing about the same
        # day is recorded as two rows to reconcile, not a silent overwrite.
        UniqueConstraint(
            "product_id", "chain_id", "snapshot_date", "source_name",
            name="uq_snapshot_grain",
        ),
        CheckConstraint(
            "granularity IN ('per_chain', 'product_total')",
            name="ck_snapshot_granularity",
        ),
        # SQLite treats NULLs as distinct inside a UNIQUE constraint, so the
        # constraint above does NOT deduplicate product-total rows (chain_id IS
        # NULL) — without this partial index, re-running an ingest would append a
        # duplicate total for the same day every time and double the headline
        # market size. The pipeline also dedupes in Python; this is the backstop.
        Index(
            "uq_snapshot_product_total",
            "product_id", "snapshot_date", "source_name",
            unique=True,
            sqlite_where=text("chain_id IS NULL"),
        ),
        Index("idx_snapshots_date", "snapshot_date"),
        Index("idx_snapshots_product_date", "product_id", "snapshot_date"),
    )

    snapshot_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.product_id"), nullable=False
    )
    # NULL chain_id means the row is a product-wide total.
    chain_id: Mapped[Optional[int]] = mapped_column(ForeignKey("chains.chain_id"))
    snapshot_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    granularity: Mapped[str] = mapped_column(String, nullable=False)

    # Stored as TEXT because a uint256 supply overflows SQLite's signed 64-bit
    # INTEGER. Converted back to int on read.
    total_supply_raw: Mapped[Optional[str]] = mapped_column(Text)
    tvl_usd: Mapped[Optional[float]] = mapped_column(Float)
    nav_per_token: Mapped[Optional[float]] = mapped_column(Float)
    holder_count: Mapped[Optional[int]] = mapped_column(Integer)
    apy_7day: Mapped[Optional[float]] = mapped_column(Float)
    apy_30day: Mapped[Optional[float]] = mapped_column(Float)

    source_name: Mapped[str] = mapped_column(String, nullable=False)
    ingested_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)


class IngestionRun(Base):
    """Recorded so a source that starts failing shows up as a visible gap rather
    than a flat line that reads as real market data."""

    __tablename__ = "ingestion_runs"
    __table_args__ = (
        CheckConstraint(
            "run_status IN ('running', 'succeeded', 'partial', 'failed')",
            name="ck_run_status",
        ),
    )

    run_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_name: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)
    run_status: Mapped[str] = mapped_column(String, nullable=False)
    rows_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text)


class StaleSchemaError(RuntimeError):
    """Raised when an existing database predates a schema change.

    There is no migration tooling here on purpose: every row is re-derivable from
    DefiLlama in one ingest, so deleting and refetching is cheaper than maintaining
    Alembic for a project this size. What that trades away is a helpful error, which
    this supplies — the raw failure would otherwise be a bare 'no such column'.
    """


def _assert_schema_is_current(engine) -> None:
    from sqlalchemy import inspect

    inspector = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if table.name not in inspector.get_table_names():
            # create_all will have made it; nothing to compare against.
            continue
        existing_columns = {column["name"] for column in inspector.get_columns(table.name)}
        expected_columns = {column.name for column in table.columns}
        missing_columns = expected_columns - existing_columns
        if missing_columns:
            raise StaleSchemaError(
                f"Table '{table.name}' is missing {sorted(missing_columns)}. This "
                "database predates the current schema. Delete it and re-ingest — the "
                "full history refetches from DefiLlama in one run, so nothing is lost:\n"
                "  rm data/treasuries.sqlite\n"
                "  treasury-dashboard init-db\n"
                "  treasury-dashboard ingest --source defillama"
            )


def build_engine(database_path: Path = DEFAULT_DATABASE_PATH, echo_sql: bool = False):
    """Create the engine and the parent directory, and create tables if absent.

    ':memory:' is passed straight through so tests can use an in-memory database
    without touching disk.
    """
    if str(database_path) == ":memory:":
        engine = create_engine("sqlite:///:memory:", echo=echo_sql)
    else:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(f"sqlite:///{database_path}", echo=echo_sql)

    Base.metadata.create_all(engine)
    _assert_schema_is_current(engine)
    return engine


def open_session(engine) -> Session:
    return Session(engine)
