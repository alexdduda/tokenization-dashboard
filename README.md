# Tokenized Treasury Dashboard

Tracks and visualizes tokenized US Treasury products — BUIDL, USYC, OUSG, USDY, BENJI
and WTGXX — across the chains they're issued on, with daily snapshots so the numbers
show trends rather than a single moment.

**Build status: stage 1 complete and verified against live data (2026-08-08).** One
ingest pulled **18,451 snapshot rows across 1,290 days** of history for four products,
totalling **$9.81B**. The UI does not exist yet, by design — the brief calls for
verified ingestion first.

Verified in that run:

| Product | TVL | 7d APY | Chains |
|---|---|---|---|
| BUIDL | $3.51B | 3.56% | 8 |
| USYC | $3.00B | 3.06% | 2 |
| OUSG | $2.52B | 3.44% | 10 |
| WTGXX | $775.8M | — | 2 |
| USDY | booked under OUSG | 3.55% | — |
| BENJI | not covered by DefiLlama | — | — |

Two behaviours worth noting in that output, because both are the schema working
rather than gaps: Ondo appears **once** at $2.52B (see [shared
slugs](#shared-slugs-and-why-ondo-appears-once)), and a second identical ingest wrote
the same 18,451 rows and produced the same $9.81B total instead of doubling it.

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

## Shared slugs, and why Ondo appears once

DefiLlama reports TVL **per protocol, not per product**. There is no `ondo-ousg` or
`ondo-usdy` — a single `ondo-yield-assets` entry covers both, so naively mapping that
slug onto both products would book $2.52B twice and show Ondo at $5.04B.

`record_tvl_from_slug` resolves it: OUSG books the combined figure, USDY sets it
`false` and contributes APY only. The registry **refuses to load** if two products
claim the same slug for TVL, because that mistake yields a plausible wrong number
rather than an error.

The same caveat applies upward: the `wisdomtree` slug is that issuer's entire on-chain
footprint and may include digital funds beyond WTGXX, so treat its TVL as an upper
bound until confirmed against WisdomTree's own reporting.

## Products no API covers

BENJI forced a third option. DefiLlama has no entry for it, and Franklin Templeton
publishes FOBXX's AUM only on its own site — so the choices were to leave it
permanently blank or to invent a number.

Instead there is a `manual` source: a figure a human copies from the issuer, which
**must** carry the URL it came from and the date it was true. Missing either is a
validation error, because a hand-typed number with no provenance is indistinguishable
from a guess six months later.

```jsonc
"manual_tvl_usd": 828000000,
"manual_tvl_as_of": "2026-08-04",
"manual_tvl_source_url": "https://www.franklintempleton.com/..."
```

```bash
treasury-dashboard ingest --source manual
```

Four deliberate limits make this safe to trust exactly as much as it deserves:

- **It expires.** After 60 days the ingest skips the figure and says how old it is and
  where to refresh it. A visibly missing number gets updated; a quietly stale one does not.
- **It never enters the historical series, and so never the headline total.** A
  point-in-time figure has no history behind it, and counting it would make the
  headline disagree with the endpoint of the chart.
- **It loses to a live source.** If an aggregator covers the product, that number wins.
  Setting a manual figure on a product that already books TVL from a slug is rejected.
- **It is labelled in the UI** — the table shows "manual, as of ⟨date⟩", and the
  coverage panel lists these products separately from measured ones.

## What "market size" does and does not mean

The headline total is **the sum of tracked products, not the whole tokenized Treasury
market.** The four covered products come to $9.81B against a market around $15B in
mid-2026 — roughly two thirds. DefiLlama's RWA category lists many more Treasury
products (Spiko, Invesco USTB, OpenEden TBILL, VanEck Treasury Fund, Hastra, and
others), and it also contains assets that are **not** Treasuries at all — Tether Gold
and Paxos Gold are ~$5B of gold sitting in the same category. Nothing here sums the
category wholesale, and the UI should label the metric for what it is.

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

### Frontend

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
npm run build      # type-checks with tsc, then bundles to frontend/dist
```

A sample payload is checked in at `frontend/public/data/dashboard.json` so the app
renders before you have run an ingest; it is flagged `is_sample_data`, and the UI shows
a banner saying so until `treasury-dashboard export` overwrites it with live figures.

Chart colors come from a palette validated for colorblind separation, lightness band,
chroma floor and 3:1 contrast against **both** the light and dark surfaces. Dark mode is
a separately chosen set of steps, not an inversion. Do not substitute hues without
re-validating them as a set.

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

## Contract addresses, and verifying them without trusting anyone

The on-chain reader is the layer that makes a number unfakeable — but only if it reads
the *right* contract. A wrong address returns a perfectly real `totalSupply()` for some
other token, which is worse than no data, so addresses ship as `null` with
`address_verified: false` and the reader skips anything unverified.

Two commands close the gap:

```bash
pip install -e ".[onchain]"
treasury-dashboard discover-addresses   # pull candidates from DefiLlama
treasury-dashboard verify-addresses     # read the chain, reconcile, report
treasury-dashboard verify-addresses --promote   # trust only what reconciled exactly
```

`verify-addresses` is the interesting one. Rather than asking you to eyeball a block
explorer, it reads `totalSupply()` and `decimals()` off each candidate and compares the
implied value against the TVL the aggregator independently reports for that product on
that chain. Two sources describing the same token agree; a wrong contract almost never
does.

What it can and cannot conclude:

| Verdict | Meaning |
|---|---|
| `confirmed` | Stable-NAV product whose supply matches reported TVL within 15%. Conclusive; `--promote` marks it verified. |
| `plausible` | Accruing-NAV product whose reported TVL exceeds supply by an amount yield can explain. Consistent, **not** proof — stays a human decision. |
| `mismatch` | The numbers disagree. Almost certainly the wrong contract. Never promoted. |
| `no_reference` | No aggregator TVL or no RPC to compare against. Not a pass. |

The asymmetry is deliberate: only stable-NAV products can be pinned exactly, because an
accruing token is worth more than $1 by an unknown accrued amount. `--promote` also
overwrites `token_decimals` with what the contract itself reports, since a wrong
decimals value silently rescales TVL by a power of ten.

DefiLlama only publishes a protocol's *primary* token address, so discovery will not
fill every chain. The rest need each issuer's own documentation; `discover-addresses`
prints the explorer link for every candidate and lists what is still missing.

## Deployment

The site is fully static — HTML, CSS, JS and one JSON file, no server and no API — so it
is published to **GitHub Pages** by `.github/workflows/ingest-and-deploy.yml`. That one
workflow runs the whole loop daily: test, ingest, commit the snapshot, build the page,
publish. No third-party hosting account is involved.

One-time setup: **Settings → Pages → Source: GitHub Actions**, on a public repo (Pages
on private repos requires a paid plan). The site then lands at
`https://<user>.github.io/tokenization-dashboard/` and refreshes itself every morning.

Ingest and deploy live in one workflow deliberately. Splitting them would make the deploy
side re-checkout the commit the ingest side had just pushed, which races; keeping them
together means the built files are already on disk. The loop that this setup invites —
the job pushes to `main`, which would retrigger the job — does not happen, because
GitHub does not trigger workflows from pushes authored by `GITHUB_TOKEN`.

Vite is configured with `base: "./"` so the bundle works from the Pages subpath, from a
domain root, or opened off disk. Nothing about the build assumes Pages, so moving to
Vercel or Netlify later is a settings change, not a code change.

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
- [x] **Stage 1b** — verified against live endpoints; 9 of 11 products covered
- [x] **Stage 2** — daily snapshot job (GitHub Actions cron, commits the SQLite file
      plus derived JSON — free, no cold starts, and the history lives in git)
- [x] **Stage 3** — React + Vite + Recharts UI
- [x] **Stage 4** — GENIUS Act context panel
- [x] **Stage 5** — deploy: GitHub Pages, published by the same workflow that ingests
- [ ] **Open** — BENJI coverage (no DefiLlama entry; needs Franklin Templeton's own
      data), and contract-address verification to switch on the on-chain reader

Scope and decisions: [`BRIEF.md`](BRIEF.md),
[`docs/data-sources-and-schema.md`](docs/data-sources-and-schema.md).
