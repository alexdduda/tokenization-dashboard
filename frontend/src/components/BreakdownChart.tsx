import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatUsd } from "../format";

interface BreakdownDatum {
  label: string;
  tvl_usd: number;
}

interface Props {
  title: string;
  note: string;
  data: BreakdownDatum[];
}

interface TooltipProps {
  active?: boolean;
  payload?: { payload: BreakdownDatum }[];
}

function BreakdownTooltip({ active, payload }: TooltipProps) {
  if (!active || !payload?.length) return null;
  const datum = payload[0].payload;
  return (
    <div className="tooltip">
      <div className="tooltip-title">{datum.label}</div>
      <div className="tooltip-row">
        <strong>{formatUsd(datum.tvl_usd)}</strong>
      </div>
    </div>
  );
}

/**
 * Ranked horizontal bars for share of the market.
 *
 * Bars over a pie because comparing lengths on a shared baseline is far more precise
 * than comparing angles, and there are more categories here than a pie tolerates.
 *
 * Deliberately one hue rather than a categorical palette: the bar's *position* already
 * identifies the issuer, so coloring each bar differently would encode identity twice
 * and burn CVD-safe slots for no gain. Length carries the magnitude; direct labels
 * carry the value.
 */
export function BreakdownChart({ title, note, data }: Props) {
  // Height grows with the category count so bars keep a readable thickness instead of
  // being squeezed into a fixed box.
  const chartHeight = Math.max(180, data.length * 34 + 30);

  return (
    <section className="card">
      <h2>{title}</h2>
      <p className="card-note">{note}</p>
      <ResponsiveContainer width="100%" height={chartHeight}>
        <BarChart
          data={data}
          layout="vertical"
          margin={{ top: 4, right: 64, bottom: 4, left: 4 }}
        >
          <CartesianGrid stroke="var(--gridline)" horizontal={false} />
          <XAxis type="number" hide />
          <YAxis
            type="category"
            dataKey="label"
            width={116}
            stroke="var(--baseline)"
            tickLine={false}
            axisLine={false}
          />
          <Tooltip
            content={<BreakdownTooltip />}
            cursor={{ fill: "var(--page-plane)" }}
          />
          <Bar dataKey="tvl_usd" radius={[0, 4, 4, 0]} barSize={18}>
            {data.map((datum) => (
              <Cell key={datum.label} fill="var(--series-1)" />
            ))}
            <LabelList
              dataKey="tvl_usd"
              position="right"
              formatter={(value: number) => formatUsd(value)}
              style={{ fill: "var(--text-secondary)", fontSize: 11.5 }}
            />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </section>
  );
}
