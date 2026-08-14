import { useEffect, useState } from "react";

import { BreakdownChart } from "./components/BreakdownChart";
import { ContextPanel } from "./components/ContextPanel";
import { CoveragePanel } from "./components/CoveragePanel";
import { MarketSizeChart } from "./components/MarketSizeChart";
import { ProductTable } from "./components/ProductTable";
import { YieldComparison } from "./components/YieldComparison";
import { formatDate, formatPercent, formatUsd } from "./format";
import type { DashboardPayload } from "./types";

// Relative so the app works from a subpath deploy as well as the domain root.
const DATA_URL = "./data/dashboard.json";

type LoadState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; payload: DashboardPayload };

function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0
    ? (sorted[middle - 1] + sorted[middle]) / 2
    : sorted[middle];
}

export function App() {
  const [state, setState] = useState<LoadState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;

    fetch(DATA_URL)
      .then((response) => {
        if (!response.ok) {
          throw new Error(`${response.status} ${response.statusText}`);
        }
        return response.json() as Promise<DashboardPayload>;
      })
      .then((payload) => {
        if (!cancelled) setState({ status: "ready", payload });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState({
          status: "error",
          message: error instanceof Error ? error.message : "Unknown error",
        });
      });

    return () => {
      cancelled = true;
    };
  }, []);

  if (state.status === "loading") {
    return <div className="state-message">Loading…</div>;
  }

  if (state.status === "error") {
    return (
      <div className="state-message">
        <p>Could not load dashboard data ({state.message}).</p>
        <p className="footnote">
          Generate it with <code>treasury-dashboard ingest --source defillama</code> then{" "}
          <code>treasury-dashboard export</code>.
        </p>
      </div>
    );
  }

  const { payload } = state;
  const reportedYields = payload.products
    .map((product) => product.apy_7day)
    .filter((apy): apy is number => apy !== null);
  const medianYield = median(reportedYields);

  return (
    <div className="page">
      {payload.is_sample_data && (
        <div className="sample-banner">
          <strong>Sample data.</strong> This is the checked-in example payload so the app
          renders before you run an ingest. Run{" "}
          <code>treasury-dashboard ingest --source defillama &amp;&amp; treasury-dashboard export</code>{" "}
          to replace it with live figures.
        </div>
      )}

      <header className="masthead">
        <h1>Tokenized Treasury Dashboard</h1>
        <p>
          Tokenized US Treasury products across on-chain issuers, snapshotted daily.
          Data as of {formatDate(payload.as_of_date)}.
        </p>
      </header>

      <div className="hero-row">
        <div className="stat-tile">
          <div className="label">Tracked value</div>
          <div className="value">{formatUsd(payload.market_total_usd)}</div>
          <div className="sub">
            across {payload.coverage.products_counted} of{" "}
            {payload.coverage.products_tracked} products
          </div>
        </div>
        <div className="stat-tile">
          <div className="label">Median 7d yield</div>
          <div className="value">{formatPercent(medianYield)}</div>
          <div className="sub">{reportedYields.length} products reporting</div>
        </div>
        <div className="stat-tile">
          <div className="label">History</div>
          <div className="value">{payload.history_days.toLocaleString()}</div>
          <div className="sub">days of daily snapshots</div>
        </div>
        <div className="stat-tile">
          <div className="label">Chains</div>
          <div className="value">{payload.chain_breakdown.length}</div>
          <div className="sub">carrying tracked products</div>
        </div>
      </div>

      <MarketSizeChart
        series={payload.market_size_series}
        productsCounted={payload.coverage.products_counted}
      />

      <div className="card-grid">
        <BreakdownChart
          title="By issuer"
          note="Share of tracked value. Slices sum to the headline figure."
          data={payload.issuer_breakdown.map((slice) => ({
            label: slice.issuer_name,
            tvl_usd: slice.tvl_usd,
          }))}
        />
        <BreakdownChart
          title="By chain"
          note="Where tracked value actually settles."
          data={payload.chain_breakdown.map((slice) => ({
            label: slice.chain_name,
            tvl_usd: slice.tvl_usd,
          }))}
        />
      </div>

      <YieldComparison products={payload.products} />
      <ProductTable products={payload.products} />
      <CoveragePanel
        coverage={payload.coverage}
        dataSources={payload.data_sources}
        lastRun={payload.last_ingestion_run}
      />
      <ContextPanel />

      <p className="footnote">
        Generated {new Date(payload.generated_at).toLocaleString()}. Not investment
        advice; figures are aggregated from third-party data and may lag issuer
        reporting.
      </p>
    </div>
  );
}
