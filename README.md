# Tokenized Treasury Dashboard

Tracks and visualizes tokenized US Treasury products — BUIDL, USYC, OUSG, USDY, BENJI
and WTGXX — across the chains they're issued on, with daily snapshots so the numbers
show trends rather than a single moment.

**Build status: stage 1 of 5 (ingestion layer).** The normalization layer and schema
are implemented and tested; the ingestion pipeline has not yet been run against live
endpoints (see [Verifying against live data](#verifying-against-live-data)). The UI
does not exist yet, by design — the brief calls for verified ingestion first.

---

## Why these data sources

The obvious answer, rwa.xyz, doesn't survive the project's no-paid-keys constraint:
its free plan is the web UI plus three exports a month, and **the API sits behind a
$500/seat/month Pro plan**. So it's used the way it's actually usable — as a periodic
manual export to *reconcile* our numbers against — and something free is the
programmatic primary.

| Source | Auth | What it gives us | Where it falls short |
|---|---|---|---|
| **DefiLlama** (`api.llama.fi`) | none | TVL now and full history, per-chain breakdown | no holder counts, thin APY coverage |
| **Direct contract reads** (`totalSupply()`) | none via public RPC | ground-truth supply per chain | needs a verified address per deployment; EVM only in v1 |
| **DefiLlama Yields** (`yields.llama.fi`) | none | APY where covered | patchy for these specific funds |
| **rwa.xyz manual export** | free acct, 3/mo | reconciliation benchmark | manual |

Every snapshot row records which source produced it, so two sources disagreeing
becomes visible data to reconcile rather than a silent overwrite.

## The one piece of domain modelling that matters most

These products do not all price the same way, and treating them alike would produce
confidently wrong numbers:

- **BUIDL, BENJI, WTGXX** hold a **$1.00 NAV** and pay yield by minting additional
  tokens. For these, `supply × $1` is a legitimate TVL figure.
- **USYC, USDY, OUSG** have an **accruing NAV** — the token is worth more than $1 by
  exactly the interest accrued since inception. For these, `supply × $1` understates
  TVL by the entire accrued yield, so the pipeline records supply and deliberately
  leaves TVL `NULL` for a source that reports NAV or AUM directly.

That distinction lives in `nav_model` in `config/products.json` and is enforced in
`sources/onchain.py::supply_to_tvl_usd`.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # add ",onchain" for the web3 contract reader

treasury-dashboard init-db       # create SQLite + load the product registry
treasury-dashboard discover-slugs  # list DefiLlama RWA protocols (needs network)
# → copy slugs into config/products.json, set slug_verified: true
treasury-dashboard ingest --source defillama
treasury-dashboard status
```

`pytest` runs the whole suite offline — no network, no API keys.

## Verifying against live data

The pipeline was developed in a sandbox with no outbound HTTPS to data providers, so
the network-touching paths are **written but not yet exercised**. Two steps to close
that, in order:

1. `treasury-dashboard discover-slugs` — confirms the DefiLlama endpoint is reachable
   and gives you the real slugs. The registry ships with `defillama_slug: null` for
   every product deliberately: a *guessed* slug returns a valid-looking payload for
   the wrong protocol, which is worse than no data.
2. `treasury-dashboard ingest --source defillama && treasury-dashboard status` —
   `status` warns if the market total falls outside a $5B–$40B plausibility band.
   For calibration: tokenized Treasuries crossed **$10B in Feb 2026** and were around
   **$14.8B in May 2026**. A total far outside that means the pipeline is wrong, not
   the market.

Contract addresses ship as `null` with `address_verified: false` for the same reason.
Fill them in from each issuer's own documentation, confirm on the chain explorer, then
flip the flag — the on-chain reader skips anything unverified unless you pass
`--allow-unverified`.

## Modules

| Path | Responsibility |
|---|---|
| `config/products.json` | The product/chain registry. Data, not code, so corrections need no code change. |
| `src/treasury_dashboard/models.py` | The normalized contract. Every source produces `NormalizedSnapshot` and nothing else, so a new source can't widen the schema. |
| `src/treasury_dashboard/database.py` | SQLite schema (SQLAlchemy). Mirrors the DDL in `docs/data-sources-and-schema.md`. |
| `src/treasury_dashboard/registry.py` | Loads and validates the registry, syncs it into the DB dimensions. Idempotent. |
| `src/treasury_dashboard/sources/defillama.py` | HTTP fetch + pure normalization for TVL history and yields. |
| `src/treasury_dashboard/sources/onchain.py` | `totalSupply()` reads over public RPC, and the NAV-model logic above. |
| `src/treasury_dashboard/pipeline.py` | Runs a source, persists snapshots, records the run. The only layer that knows about DB ids. |
| `src/treasury_dashboard/queries.py` | The four read queries backing the planned dashboard views. |
| `src/treasury_dashboard/cli.py` | `init-db`, `discover-slugs`, `ingest`, `status`. |

## Schema notes

Grain of the fact table is **one row per product, per chain, per day, per source**.
Chain-level is the finest grain any source reports and rolls up cleanly to product and
market level.

Three details that are load-bearing rather than incidental:

- **`granularity`** (`per_chain` / `product_total`). Both are stored from the same
  payload. Summing them together would count every product twice, so every aggregate
  in `queries.py` filters on exactly one. There's a test that fails if that filter is
  dropped.
- **`total_supply_raw` is TEXT.** A `uint256` supply overflows SQLite's signed 64-bit
  `INTEGER`.
- **A partial unique index covers product totals.** SQLite treats `NULL`s as distinct
  inside a `UNIQUE` constraint, so `UNIQUE(product_id, chain_id, snapshot_date,
  source_name)` does *not* deduplicate rows where `chain_id IS NULL` — without the
  partial index, every re-run would append a duplicate total and inflate the headline
  market size. The pipeline also dedupes in Python; the index is the backstop.

`ingestion_runs` records every run's status and row count so a source that starts
failing shows up as a visible gap rather than a flat line that reads as real market
data.

## Roadmap

- [x] **Stage 1** — ingestion layer, normalized schema, tests
- [ ] **Stage 1b** — verify against live endpoints, fill slugs and contract addresses
- [ ] **Stage 2** — daily snapshot job (GitHub Actions cron, commits the SQLite file
      plus derived JSON — free, no cold starts, and the history lives in git)
- [ ] **Stage 3** — React + Vite + Recharts UI: market size over time, issuer
      breakdown, yield comparison, sortable product table
- [ ] **Stage 4** — GENIUS Act context panel
- [ ] **Stage 5** — deploy (static frontend on Vercel)

Scope and decisions: [`BRIEF.md`](BRIEF.md),
[`docs/data-sources-and-schema.md`](docs/data-sources-and-schema.md).
