# Stage 0 — Proposed data sources & schema

Status: **confirmed 2026-08-08.** The source hierarchy in §2 and the free-hosting
architecture in §5a were both signed off, and stage 1 (the ingestion layer) is built
against this document. Schema changes from here need a matching edit in
`src/treasury_dashboard/database.py`.

---

## 1. Stack, and why

| Layer | Choice | Why this one |
|---|---|---|
| Ingestion + normalization | **Python 3.11**, `httpx`, `web3.py`, `pydantic` | The substance of this project is the ingestion layer. `web3.py` reads `totalSupply()` off an ERC-20 in ~5 lines; `pydantic` models make the normalization contract *enforced* rather than conventional, which is exactly the thing an interviewer pokes at. |
| Storage | **SQLite** via `SQLAlchemy` | Brief calls for it, and a single file is the only datastore that survives free-tier hosting (see §5). |
| API (local dev / option B) | **FastAPI** | Same Pydantic models serve as both the DB contract and the response schema — no duplicate type definitions. |
| Frontend | **React + Vite + TypeScript**, **Recharts** | Portable, free on Vercel. Recharts covers all three chart types declaratively; d3 would be more code for no gain at this scale. |
| Scheduler | **GitHub Actions cron** | See §5 — this is the load-bearing choice. |

TypeScript over plain JS specifically because the normalized schema is the interesting part
of this project; having it typed end-to-end is the demonstration.

---

## 2. Data sources — corrected against what's actually reachable

The brief names **rwa.xyz as the primary source**. That does not survive contact with the
no-paid-keys constraint, and this is worth knowing before any code is written:

> **rwa.xyz free plan = web UI + 3 data exports/month. The API is gated behind Pro at
> $500/seat/month.** ([pricing](https://app.rwa.xyz/pricing))

So rwa.xyz cannot be the *programmatic* primary. It stays in the project, demoted to a role
it's actually good at — a monthly manual CSV export used to **reconcile** our numbers, plus a
README citation as the industry reference. "I validated my pipeline against rwa.xyz" is a
better interview line than "I called an API."

Revised source hierarchy:

| Rank | Source | Auth | Gives us | Known weakness |
|---|---|---|---|---|
| 1 | **DefiLlama** — `api.llama.fi/protocols`, `/protocol/{slug}`, and the [RWA dashboard](https://defillama.com/rwa) | none | TVL now + full history, per-chain breakdown, issuer mapping | no holder counts, thin APY coverage |
| 2 | **Direct on-chain reads** — `totalSupply()` / `decimals()` per deployment via public RPC | none (public RPC) or free Alchemy/Infura key | ground-truth supply, per-chain, unfakeable | needs a contract address per product per chain (the setup cost) |
| 3 | **DefiLlama Yields** — `yields.llama.fi/pools` | none | APY where covered | patchy for these specific funds |
| 4 | **Issuer endpoints** — Ondo, Securitize/BUIDL, Franklin Templeton BENJI | none | highest fidelity, incl. NAV and holder counts | bespoke per issuer, breaks when they redesign |
| 5 | **rwa.xyz manual export** | free acct, 3/mo | reconciliation benchmark | manual, not automatable on free tier |

Every snapshot row records which source produced it, so the dashboard can show provenance and
a disagreement between sources becomes visible data rather than a silent overwrite.

### Products in scope (v1)

BUIDL (BlackRock/Securitize), USYC (Circle, via the Hashnote acquisition), OUSG (Ondo),
USDY (Ondo), BENJI (Franklin Templeton — the on-chain share class of FOBXX), WTGXX
(WisdomTree).

### Two factual corrections to the brief

Both matter because the brief is a document you'll speak to in interviews:

1. **BUIDL is no longer the largest single product.** Circle's USYC passed it in January 2026
   (~$1.69B vs ~$1.68B at the crossover) and reporting through mid-2026 puts USYC ahead.
   Sources disagree on exact AUM by a wide margin, which is itself a good reason to build the
   reconciliation step in §2.
2. **BENJI vs FOBXX** are not the same thing — BENJI is the token; FOBXX is the SEC-registered
   1940 Act fund it represents. Getting that distinction right in the UI is cheap credibility.

Market context for sanity-checking the pipeline: total tokenized Treasuries crossed **$10B in
February 2026** and sat near **$14.8B across ~82 assets in May 2026**. If our totals land
outside roughly $10–20B, the pipeline is wrong, not the market.

---

## 3. Schema

Grain of the fact table: **one row per product, per chain, per day.**

Chain-level is the finest grain any source reports, and it rolls up cleanly to product-level
and market-level. Choosing the coarser grain first would have made "BUIDL across nine chains"
unrepresentable later without a migration.

```sql
-- Slow-changing dimension: one row per tokenized product.
CREATE TABLE products (
    product_id       INTEGER PRIMARY KEY,
    symbol           TEXT    NOT NULL UNIQUE,        -- 'BUIDL'
    display_name     TEXT    NOT NULL,               -- 'BlackRock USD Institutional Digital Liquidity Fund'
    issuer_name      TEXT    NOT NULL,               -- 'BlackRock'
    platform_name    TEXT,                           -- 'Securitize' — issuer and platform differ, and the
                                                     -- distinction is the whole tokenization value chain
    legal_wrapper    TEXT,                           -- '1940 Act fund' | 'BVI note' | 'Reg D private fund'
    underlying_asset TEXT    NOT NULL,               -- 'US T-bills + overnight repo'
    inception_date   DATE,
    is_accredited_only INTEGER NOT NULL DEFAULT 0,   -- retail accessibility is a real differentiator here
    homepage_url     TEXT,
    -- 'stable_one_dollar' | 'accruing'. Decides whether token supply can be converted
    -- to USD at all: accruing-NAV products are worth more than $1 by the accrued
    -- interest, so supply * $1 would systematically understate them.
    nav_model        TEXT    NOT NULL,
    defillama_slug   TEXT
);

CREATE TABLE chains (
    chain_id     INTEGER PRIMARY KEY,
    chain_name   TEXT NOT NULL UNIQUE,               -- 'Ethereum'
    evm_chain_id INTEGER,                            -- NULL for non-EVM (Solana, Aptos, Sui)
    explorer_url TEXT
);

-- One product lives on many chains. This table is what makes "BUIDL on nine chains" a
-- queryable fact instead of a sentence in a README.
CREATE TABLE product_deployments (
    deployment_id    INTEGER PRIMARY KEY,
    product_id       INTEGER NOT NULL REFERENCES products(product_id),
    chain_id         INTEGER NOT NULL REFERENCES chains(chain_id),
    contract_address TEXT,                           -- NULL where the product is off-chain-reported only
    token_decimals   INTEGER,
    -- Unverified addresses are stored so the gap is visible, but the on-chain reader
    -- skips them: a real-looking number for the wrong token is worse than no number.
    address_verified INTEGER NOT NULL DEFAULT 0,
    first_seen_date  DATE,
    UNIQUE (product_id, chain_id)
);

-- The fact table. One row per product, per chain (or per product total), per day.
CREATE TABLE snapshots (
    snapshot_id     INTEGER PRIMARY KEY,
    product_id      INTEGER NOT NULL REFERENCES products(product_id),
    chain_id        INTEGER          REFERENCES chains(chain_id),  -- NULL => product-wide total
    snapshot_date   DATE    NOT NULL,

    -- granularity guards against the one bug that would silently corrupt every headline
    -- number: summing per-chain rows AND a product-total row for the same product/day.
    -- Aggregate queries must filter on exactly one granularity.
    granularity     TEXT    NOT NULL CHECK (granularity IN ('per_chain', 'product_total')),

    total_supply_raw TEXT,                           -- TEXT: uint256 overflows SQLite INTEGER
    tvl_usd          REAL,
    nav_per_token    REAL,
    holder_count     INTEGER,
    apy_7day         REAL,
    apy_30day        REAL,

    source_name      TEXT    NOT NULL,               -- 'defillama' | 'onchain_rpc' | 'issuer_ondo' | ...
    ingested_at      TIMESTAMP NOT NULL,
    UNIQUE (product_id, chain_id, snapshot_date, source_name)
);

-- SQLite treats NULLs as distinct inside a UNIQUE constraint, so the constraint above
-- does NOT deduplicate product-total rows (chain_id IS NULL). Without this partial
-- index, every re-run appends a duplicate total and inflates the headline market size.
CREATE UNIQUE INDEX uq_snapshot_product_total
    ON snapshots (product_id, snapshot_date, source_name)
    WHERE chain_id IS NULL;

CREATE INDEX idx_snapshots_date ON snapshots (snapshot_date);
CREATE INDEX idx_snapshots_product_date ON snapshots (product_id, snapshot_date);

-- Ingestion runs are recorded so a silently-failing source shows up as a gap in the UI
-- rather than a flat line that looks like real market data.
CREATE TABLE ingestion_runs (
    run_id        INTEGER PRIMARY KEY,
    source_name   TEXT NOT NULL,
    started_at    TIMESTAMP NOT NULL,
    finished_at   TIMESTAMP,
    run_status    TEXT NOT NULL CHECK (run_status IN ('running', 'succeeded', 'partial', 'failed')),
    rows_written  INTEGER NOT NULL DEFAULT 0,
    error_message TEXT
);
```

Each of the four dashboard views in the brief maps to one query over `snapshots`:

- **market size over time** → `SUM(tvl_usd) GROUP BY snapshot_date WHERE granularity='per_chain'`
- **breakdown by issuer** → join `products`, group by `issuer_name`, latest date
- **yield comparison** → latest `apy_7day` per product
- **sortable table** → latest snapshot per product, joined to `products`

---

## 4. Build order

Unchanged from the brief. Stage 1 (ingestion) is verified against live data and shown as
working output before any UI exists.

---

## 5. Two constraints that change the architecture

**a) Free-tier hosting eats SQLite.** Render/Railway free tiers have ephemeral disk and spin
down when idle — a SQLite file written by a cron job there is gone by morning, which
silently defeats feature 4 (historical tracking), the feature that makes this more than a
screenshot.

Recommended instead: **GitHub Actions cron runs the ingestion daily, commits the SQLite file
plus a small derived JSON to the repo, and the frontend is a static Vercel deploy reading
that JSON.** Cost is zero, there are no cold starts, the daily snapshot history is literally
in git history (a nice thing to show), and the FastAPI backend still exists for local
development. Option B — always-on backend on Render — remains available if you'd rather demo
a live API surface.

**b) This build environment has no network egress to data APIs.** Confirmed by probe:
`api.llama.fi`, `defillama.com`, `app.rwa.xyz`, public RPC endpoints, and `api.coingecko.com`
all fail CONNECT with 403 under the environment's network policy; only GitHub and package
registries are reachable. Ingestion code can be written and committed here, but "get data
ingestion working and *verified*" has to happen either on your machine or after the
environment's network policy is widened.
