import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatPercent } from "../format";
import type { ProductRow } from "../types";

interface Props {
  products: ProductRow[];
}

interface YieldDatum {
  symbol: string;
  apy_7day: number;
  apy_30day: number | null;
}

interface TooltipProps {
  active?: boolean;
  payload?: { payload: YieldDatum }[];
}

function YieldTooltip({ active, payload }: TooltipProps) {
  if (!active || !payload?.length) return null;
  const datum = payload[0].payload;
  return (
    <div className="tooltip">
      <div className="tooltip-title">{datum.symbol}</div>
      <div className="tooltip-row">
        <span className="swatch" style={{ background: "var(--series-1)" }} />
        7 day <strong>{formatPercent(datum.apy_7day)}</strong>
      </div>
      {datum.apy_30day !== null && (
        <div className="tooltip-row">
          <span className="swatch" style={{ background: "var(--series-2)" }} />
          30 day <strong>{formatPercent(datum.apy_30day)}</strong>
        </div>
      )}
    </div>
  );
}

/**
 * APY across products, 7 day beside 30 day.
 *
 * Two series on ONE axis — both are percentages on the same scale, so a second y-axis
 * would be indefensible. Products without a reported APY are dropped rather than
 * plotted at zero, since a Treasury fund yielding 0% would be a false claim, not a gap.
 */
export function YieldComparison({ products }: Props) {
  const yieldData: YieldDatum[] = products
    .filter((product): product is ProductRow & { apy_7day: number } =>
      product.apy_7day !== null,
    )
    .map((product) => ({
      symbol: product.symbol,
      apy_7day: product.apy_7day,
      apy_30day: product.apy_30day,
    }))
    .sort((left, right) => right.apy_7day - left.apy_7day);

  if (yieldData.length === 0) {
    return (
      <section className="card">
        <h2>Yield comparison</h2>
        <p className="card-note">No product currently reports an APY.</p>
      </section>
    );
  }

  const missingCount = products.length - yieldData.length;
  const hasThirtyDay = yieldData.some((datum) => datum.apy_30day !== null);

  return (
    <section className="card">
      <h2>Yield comparison</h2>
      <p className="card-note">
        Annualised percentage yield, highest first.
        {missingCount > 0 &&
          ` ${missingCount} tracked ${
            missingCount === 1 ? "product reports" : "products report"
          } no APY and ${missingCount === 1 ? "is" : "are"} omitted rather than shown as zero.`}
      </p>

      {/* Legend present because there are two series; identity is never color-alone. */}
      {hasThirtyDay && (
        <ul className="legend">
          <li>
            <span className="swatch" style={{ background: "var(--series-1)" }} />7 day
          </li>
          <li>
            <span className="swatch" style={{ background: "var(--series-2)" }} />
            30 day
          </li>
        </ul>
      )}

      <ResponsiveContainer width="100%" height={260}>
        <BarChart data={yieldData} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
          <CartesianGrid stroke="var(--gridline)" vertical={false} />
          <XAxis
            dataKey="symbol"
            stroke="var(--baseline)"
            tickLine={false}
            interval={0}
            angle={-30}
            textAnchor="end"
            height={58}
          />
          <YAxis
            tickFormatter={(value: number) => `${value}%`}
            stroke="var(--baseline)"
            tickLine={false}
            axisLine={false}
            width={44}
          />
          <Tooltip content={<YieldTooltip />} cursor={{ fill: "var(--page-plane)" }} />
          {/* barGap leaves the 2px surface gap between adjacent fills. */}
          <Bar
            dataKey="apy_7day"
            fill="var(--series-1)"
            radius={[4, 4, 0, 0]}
            barSize={16}
          />
          {hasThirtyDay && (
            <Bar
              dataKey="apy_30day"
              fill="var(--series-2)"
              radius={[4, 4, 0, 0]}
              barSize={16}
            />
          )}
        </BarChart>
      </ResponsiveContainer>
    </section>
  );
}
