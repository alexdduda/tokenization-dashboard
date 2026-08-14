import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatDate, formatMonthYear, formatUsd } from "../format";
import type { MarketSizePoint } from "../types";

interface Props {
  series: MarketSizePoint[];
  productsCounted: number;
}

interface TooltipProps {
  active?: boolean;
  payload?: { value: number; payload: MarketSizePoint }[];
}

function SeriesTooltip({ active, payload }: TooltipProps) {
  if (!active || !payload?.length) return null;
  const point = payload[0].payload;
  return (
    <div className="tooltip">
      <div className="tooltip-title">{formatDate(point.date)}</div>
      <div className="tooltip-row">
        <span className="swatch" style={{ background: "var(--series-1)" }} />
        <strong>{formatUsd(point.tvl_usd)}</strong>
      </div>
    </div>
  );
}

/**
 * Total TVL of counted products over time.
 *
 * One series, so no legend box — the card title names it. An area rather than a bare
 * line because the quantity is a cumulative stock that starts at zero, and the fill
 * reads as "how much exists" rather than "how it changed".
 */
export function MarketSizeChart({ series, productsCounted }: Props) {
  return (
    <section className="card">
      <h2>Tracked tokenized Treasury value over time</h2>
      <p className="card-note">
        Sum of {productsCounted} products with reported TVL. Products holding other
        tracked products are excluded, so nothing is counted twice.
      </p>
      <ResponsiveContainer width="100%" height={300}>
        <AreaChart data={series} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
          <CartesianGrid stroke="var(--gridline)" vertical={false} />
          <XAxis
            dataKey="date"
            tickFormatter={formatMonthYear}
            stroke="var(--baseline)"
            tickLine={false}
            minTickGap={44}
          />
          <YAxis
            tickFormatter={(value: number) => formatUsd(value)}
            stroke="var(--baseline)"
            tickLine={false}
            axisLine={false}
            width={62}
          />
          {/* Crosshair rather than a highlight band: the series is dense, and a band
              would imply a bucket width the daily data does not have. */}
          <Tooltip
            content={<SeriesTooltip />}
            cursor={{ stroke: "var(--text-muted)", strokeWidth: 1 }}
          />
          <Area
            type="monotone"
            dataKey="tvl_usd"
            stroke="var(--series-1)"
            strokeWidth={2}
            fill="var(--series-1-fill)"
            activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface-1)" }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </section>
  );
}
