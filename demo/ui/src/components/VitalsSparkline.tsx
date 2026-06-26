import { LineChart, Line, ResponsiveContainer, ReferenceLine, Tooltip } from 'recharts';
import type { VitalsReading } from '../types';

interface VitalsSparklineProps {
  data: VitalsReading[];
  color: string;
  threshold?: number;
  unit?: string;
}

export function VitalsSparkline({ data, color, threshold, unit }: VitalsSparklineProps) {
  if (data.length < 2) {
    return (
      <div className="h-14 flex items-center justify-center">
        <span className="text-xs text-slate-400">Collecting data…</span>
      </div>
    );
  }

  const chartData = data.map((d, i) => ({ i, value: Math.round(d.value * 10) / 10 }));

  return (
    <ResponsiveContainer width="100%" height={56}>
      <LineChart data={chartData} margin={{ top: 4, right: 4, left: 4, bottom: 4 }}>
        {threshold !== undefined && (
          <ReferenceLine
            y={threshold}
            stroke="#fca5a5"
            strokeDasharray="3 3"
            strokeWidth={1}
          />
        )}
        <Tooltip
          content={({ active, payload }) => {
            if (!active || !payload?.length) return null;
            return (
              <div className="bg-white border border-slate-200 rounded-lg px-2 py-1 shadow-md text-xs text-slate-700">
                {payload[0].value} {unit ?? ''}
              </div>
            );
          }}
        />
        <Line
          type="monotone"
          dataKey="value"
          stroke={color}
          strokeWidth={1.75}
          dot={false}
          isAnimationActive={false}
          activeDot={{ r: 3, fill: color }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
