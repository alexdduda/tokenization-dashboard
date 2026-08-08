"""Ingestion sources.

Each module exposes `SOURCE_NAME`, thin `fetch_*` functions that do HTTP or RPC and
nothing else, and pure `normalize_*` / `build_*` functions that turn a raw payload
into `NormalizedSnapshot` objects. Keeping those halves separate is what makes the
normalization layer testable offline from recorded fixtures.
"""
