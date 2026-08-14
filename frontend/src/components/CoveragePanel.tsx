import { formatUsd } from "../format";
import type { Coverage, DataSource, IngestionRunSummary } from "../types";

interface Props {
  coverage: Coverage;
  dataSources: DataSource[];
  lastRun: IngestionRunSummary | null;
}

/**
 * States what the headline number leaves out.
 *
 * This panel exists because the alternative — a big total with no qualification — would
 * be the dishonest version of this dashboard. Coverage, exclusions and provenance come
 * from the exported payload rather than being hardcoded, so they cannot drift from the
 * data.
 */
export function CoveragePanel({ coverage, dataSources, lastRun }: Props) {
  return (
    <section className="card">
      <h2>What this number does and does not include</h2>
      <p className="card-note">{coverage.caveat}</p>

      <div className="prose">
        <h3>Counted</h3>
        <p>
          {coverage.products_counted} of {coverage.products_tracked} tracked products
          report TVL and are summed into the total.
        </p>

        {coverage.products_excluded_from_total.length > 0 && (
          <>
            <h3>Excluded to avoid double counting</h3>
            <ul className="footnote">
              {coverage.products_excluded_from_total.map((excluded) => (
                <li key={excluded.symbol}>
                  <strong>{excluded.symbol}</strong>
                  {excluded.tvl_usd !== null && ` (${formatUsd(excluded.tvl_usd)})`} —{" "}
                  {excluded.reason}
                </li>
              ))}
            </ul>
          </>
        )}

        {coverage.products_without_data.length > 0 && (
          <>
            <h3>Tracked but not yet covered</h3>
            <p className="footnote">
              {coverage.products_without_data.join(", ")} — no source currently reports
              these, so they contribute nothing to the total and the real market is
              larger by their size.
            </p>
          </>
        )}

        <h3>Sources</h3>
        <ul className="footnote">
          {dataSources.map((source) => (
            <li key={source.name}>
              <a href={source.url} target="_blank" rel="noreferrer">
                {source.name}
              </a>{" "}
              — {source.role}
            </li>
          ))}
        </ul>

        {lastRun && (
          <p className="footnote">
            Last ingest: {lastRun.source_name}, {lastRun.run_status},{" "}
            {lastRun.rows_written.toLocaleString()} rows written.
            {lastRun.error_message && ` Errors: ${lastRun.error_message}`}
          </p>
        )}
      </div>
    </section>
  );
}
