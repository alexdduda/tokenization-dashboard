/**
 * Mirrors the payload produced by `treasury_dashboard/export.py`.
 *
 * These two files are the contract between the Python pipeline and this app. If you
 * change one, change the other — `npm run typecheck` will not catch a drift on the
 * Python side.
 */

export type NavModel = "stable_one_dollar" | "accruing";

export interface MarketSizePoint {
  date: string;
  tvl_usd: number;
}

export interface IssuerSlice {
  issuer_name: string;
  tvl_usd: number;
}

export interface ChainSlice {
  chain_name: string;
  tvl_usd: number;
}

export interface ProductRow {
  symbol: string;
  display_name: string;
  issuer_name: string;
  platform_name: string | null;
  legal_wrapper: string | null;
  underlying_asset: string;
  nav_model: NavModel;
  is_accredited_only: boolean;
  inception_date: string | null;
  tvl_usd: number | null;
  apy_7day: number | null;
  apy_30day: number | null;
  chain_count: number;
  counts_toward_market_total: boolean;
  market_total_exclusion_reason: string | null;
}

export interface ExcludedProduct {
  symbol: string;
  tvl_usd: number | null;
  reason: string | null;
}

export interface Coverage {
  products_counted: number;
  products_tracked: number;
  products_without_data: string[];
  products_excluded_from_total: ExcludedProduct[];
  caveat: string;
}

export interface IngestionRunSummary {
  source_name: string;
  run_status: string;
  rows_written: number;
  started_at: string;
  error_message: string | null;
}

export interface DataSource {
  name: string;
  url: string;
  role: string;
}

export interface DashboardPayload {
  generated_at: string;
  as_of_date: string;
  market_total_usd: number | null;
  history_days: number;
  coverage: Coverage;
  market_size_series: MarketSizePoint[];
  issuer_breakdown: IssuerSlice[];
  chain_breakdown: ChainSlice[];
  products: ProductRow[];
  last_ingestion_run: IngestionRunSummary | null;
  data_sources: DataSource[];
  /** Set only on the checked-in sample file, so the UI can say so. */
  is_sample_data?: boolean;
}
