# Tokenized Treasury Dashboard — Project Brief

> Captured verbatim from the project owner. This file is the source of truth for
> scope. Changes to scope go here first, then into `docs/data-sources-and-schema.md`.

Build a Tokenized Treasury Dashboard: a web app that tracks and visualizes real-world
asset (RWA) tokenization, specifically tokenized US Treasuries, across major on-chain
issuers.

## Goal

A portfolio-grade project demonstrating I understand both the technical (on-chain data,
smart contracts) and macro (tokenization economics, regulatory context) sides of RWA
tokenization. This is for internship applications in blockchain/tokenization, so code
quality and clear architecture matter as much as functionality.

## Core features (build in this order, confirm each works before moving on)

1. **Data ingestion layer**: pull on-chain data for tokenized Treasury products from the
   current market leaders: BlackRock's BUIDL (via Securitize, now live on nine chains and
   the largest single product), Circle's USYC, Ondo Finance (OUSG and USDY), Franklin
   Templeton's BENJI, and WisdomTree's WTGXX. rwa.xyz is now the standard tracker for this
   data (better coverage than DefiLlama alone as of 2026), use it as the primary source and
   fall back to on-chain contract reads via Alchemy or Infura for anything it doesn't cover.
2. **Data model**: normalize each protocol's data into a common schema (TVL, yield/APY,
   holder count, chain, underlying asset, issuance date).
3. **Dashboard UI**: a clean single-page view showing:
   - Total tokenized Treasury market size over time (line chart)
   - Breakdown by issuer (bar or pie chart)
   - Yield comparison across products
   - A sortable table of individual products with key stats
4. **Historical tracking**: store daily snapshots so the dashboard shows trends, not just a
   live snapshot (use a lightweight DB, SQLite is fine for a portfolio project).
5. **Context panel**: a short static section explaining the GENIUS Act's relevance to
   stablecoin/tokenization infrastructure, written concisely, this is what turns it from
   "a chart" into "evidence I understand the space."

## Technical requirements

- Stack: your choice, but justify it briefly before starting (I lean React frontend + a
  lightweight Python or Node backend, since that's most portable for a demo).
- Explicit, readable code: distinct variable names, concise comments explaining *why* not
  *what*, no clever one-liners that sacrifice clarity.
- Build incrementally: get data ingestion working and verified before touching the UI. Show
  me working output at each stage rather than building everything then debugging.
- Complete files, not fragments, when you hand off code for me to review.
- Include a README explaining setup, data sources, and what each module does, this doubles
  as documentation I can reference when discussing the project in interviews.

## Constraints

- No paid API keys required to run a basic version, use free tiers or public endpoints
  wherever possible.
- Deployable for free (Vercel/Netlify frontend, Railway/Render or SQLite-on-disk backend) so
  I can share a live link in applications.

## Start here

Propose the data sources and schema first, confirm with me before writing ingestion code.
Then build in the order above, one stage at a time.
