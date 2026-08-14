/** Display helpers. Kept apart from components so they can be reasoned about alone. */

/**
 * Compact USD, e.g. "$3.51B".
 *
 * Returns an em dash rather than "$0" for null, because a missing figure and a real
 * zero mean very different things here: one is a coverage gap, the other is a product
 * with no assets.
 */
export function formatUsd(amount: number | null | undefined): string {
  if (amount === null || amount === undefined) return "—";
  const absolute = Math.abs(amount);
  if (absolute >= 1e9) return `$${(amount / 1e9).toFixed(2)}B`;
  if (absolute >= 1e6) return `$${(amount / 1e6).toFixed(1)}M`;
  if (absolute >= 1e3) return `$${(amount / 1e3).toFixed(1)}K`;
  return `$${amount.toFixed(0)}`;
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(2)}%`;
}

export function formatDate(isoDate: string): string {
  // Parsed as UTC deliberately: snapshot dates are UTC calendar days, and letting the
  // browser apply a local offset would shift every label a day west of GMT.
  const parsed = new Date(`${isoDate}T00:00:00Z`);
  return parsed.toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

export function formatMonthYear(isoDate: string): string {
  const parsed = new Date(`${isoDate}T00:00:00Z`);
  return parsed.toLocaleDateString("en-US", {
    month: "short",
    year: "2-digit",
    timeZone: "UTC",
  });
}

/** Human label for the NAV model, which drives how a product's yield reaches holders. */
export function navModelLabel(navModel: string): string {
  return navModel === "stable_one_dollar" ? "$1.00 NAV" : "Accruing NAV";
}
