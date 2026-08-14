import { useMemo, useState } from "react";

import { formatPercent, formatUsd, navModelLabel } from "../format";
import type { ProductRow } from "../types";

type SortKey = "symbol" | "issuer_name" | "tvl_usd" | "apy_7day" | "chain_count";
type SortDirection = "asc" | "desc";

interface Column {
  key: SortKey;
  label: string;
  numeric: boolean;
}

const COLUMNS: Column[] = [
  { key: "symbol", label: "Product", numeric: false },
  { key: "issuer_name", label: "Issuer", numeric: false },
  { key: "tvl_usd", label: "TVL", numeric: true },
  { key: "apy_7day", label: "APY 7d", numeric: true },
  { key: "chain_count", label: "Chains", numeric: true },
];

/**
 * Whether the tokenization platform is worth naming beside the issuer.
 *
 * The interesting case is a genuine split in the value chain — BlackRock issuing,
 * Securitize tokenizing. When the platform is just the issuer's own digital arm
 * ("WisdomTree" / "WisdomTree Connect") repeating it adds noise, not information.
 */
function isDistinctPlatform(issuerName: string, platformName: string | null): boolean {
  if (!platformName) return false;
  const issuer = issuerName.toLowerCase();
  const platform = platformName.toLowerCase();
  return !platform.includes(issuer) && !issuer.includes(platform);
}

/**
 * Compare two values, always sorting nulls to the bottom.
 *
 * Nulls here mean "no coverage", not "zero". Letting them sort as 0 would rank an
 * unmeasured product below a genuinely tiny one, which misreads the data.
 */
function compareValues(
  left: string | number | null,
  right: string | number | null,
  direction: SortDirection,
): number {
  if (left === null && right === null) return 0;
  if (left === null) return 1;
  if (right === null) return -1;

  const ordering =
    typeof left === "string" && typeof right === "string"
      ? left.localeCompare(right)
      : Number(left) - Number(right);

  return direction === "asc" ? ordering : -ordering;
}

export function ProductTable({ products }: { products: ProductRow[] }) {
  const [sortKey, setSortKey] = useState<SortKey>("tvl_usd");
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");

  const sortedProducts = useMemo(() => {
    return [...products].sort((left, right) =>
      compareValues(left[sortKey], right[sortKey], sortDirection),
    );
  }, [products, sortKey, sortDirection]);

  function handleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDirection(sortDirection === "asc" ? "desc" : "asc");
      return;
    }
    setSortKey(key);
    // Numbers are most useful largest-first; names alphabetical.
    setSortDirection(key === "symbol" || key === "issuer_name" ? "asc" : "desc");
  }

  return (
    <section className="card">
      <h2>Products</h2>
      <p className="card-note">
        Every tracked product, including those with no data and those excluded from the
        headline total. Click a column to sort.
      </p>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              {COLUMNS.map((column) => (
                <th
                  key={column.key}
                  className={column.numeric ? "numeric" : undefined}
                  onClick={() => handleSort(column.key)}
                  aria-sort={
                    sortKey === column.key
                      ? sortDirection === "asc"
                        ? "ascending"
                        : "descending"
                      : undefined
                  }
                >
                  {column.label}
                  {sortKey === column.key && (sortDirection === "asc" ? " ↑" : " ↓")}
                </th>
              ))}
              <th>Structure</th>
            </tr>
          </thead>
          <tbody>
            {sortedProducts.map((product) => (
              <tr
                key={product.symbol}
                className={product.counts_toward_market_total ? undefined : "row-excluded"}
                title={product.market_total_exclusion_reason ?? undefined}
              >
                <td className="symbol-cell">
                  {product.symbol}
                  <span className="product-name">{product.display_name}</span>
                </td>
                <td>
                  {product.issuer_name}
                  {isDistinctPlatform(product.issuer_name, product.platform_name) && (
                    <span className="product-name">via {product.platform_name}</span>
                  )}
                </td>
                <td className="numeric">
                  {formatUsd(product.tvl_usd)}
                  {!product.counts_toward_market_total && (
                    <span className="product-name">not in total</span>
                  )}
                </td>
                <td className="numeric">{formatPercent(product.apy_7day)}</td>
                <td className="numeric">{product.chain_count || "—"}</td>
                <td>
                  <span className="pill">{navModelLabel(product.nav_model)}</span>{" "}
                  {product.is_accredited_only ? (
                    <span className="pill">Accredited</span>
                  ) : (
                    <span className="pill">Retail</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
