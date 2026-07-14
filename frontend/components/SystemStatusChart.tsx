import React, { useState } from 'react';
import { XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Area, AreaChart } from 'recharts';
import { useSystemStore } from '@/stores/systemStore';

const SERIES = [
  { key: 'cpu', label: 'CPU', color: '#3b82f6', axis: 'pct' as const },
  { key: 'memory', label: 'Memory', color: '#10b981', axis: 'pct' as const },
  { key: 'disk', label: 'Disk', color: '#8b5cf6', axis: 'pct' as const },
  { key: 'network', label: 'Network', color: '#f59e0b', axis: 'net' as const },
];

export function SystemStatusChart() {
  const { metrics } = useSystemStore();
  const [visible, setVisible] = useState<Record<string, boolean>>({
    cpu: true,
    memory: true,
    disk: true,
    network: true,
  });

  const toggle = (key: string) =>
    setVisible(v => ({ ...v, [key]: !v[key] }));

  // Create a lightweight timeseries from the last metrics snapshot for chart
  const times = ['T-5', 'T-4', 'T-3', 'T-2', 'T-1', 'Now'];
  const baseline = metrics || { cpu: 50, memory: 60, disk: 40, network: 20 };
  const networkMB = (baseline.network || 0) / (1024 * 1024);
  const systemSeries = times.map((t, i) => ({
    time: t,
    cpu: Math.max(0, Math.round(baseline.cpu + (i - 3) * 2)),
    memory: Math.max(0, Math.round(baseline.memory + (i - 3) * 1.5)),
    disk: Math.max(0, Math.round(baseline.disk + (i - 3) * 1)),
    network: Math.max(0, Math.round(networkMB + (i - 3) * (networkMB * 0.05))),
  }));

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <div className="text-center">
          <div className="text-2xl font-bold text-blue-600">{metrics ? `${metrics.cpu.toFixed(0)}%` : '—'}</div>
          <div className="text-sm text-gray-600">CPU Usage</div>
        </div>
        <div className="text-center">
          <div className="text-2xl font-bold text-green-600">{metrics ? `${metrics.memory.toFixed(0)}%` : '—'}</div>
          <div className="text-sm text-gray-600">Memory</div>
        </div>
        <div className="text-center">
          <div className="text-2xl font-bold text-purple-600">{metrics ? `${metrics.disk.toFixed(0)}%` : '—'}</div>
          <div className="text-sm text-gray-600">Disk</div>
        </div>
        <div className="text-center">
          <div className="text-2xl font-bold text-orange-600">{metrics ? `${(metrics.network / (1024 * 1024)).toFixed(0)}MB` : '—'}</div>
          <div className="text-sm text-gray-600">Network</div>
        </div>
      </div>

      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={systemSeries}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e0e0e0" />
            <XAxis dataKey="time" stroke="#666" fontSize={12} />
            <YAxis
              yAxisId="pct"
              stroke="#666"
              fontSize={12}
              domain={[0, 100]}
              tickFormatter={(v) => `${v}%`}
            />
            <YAxis
              yAxisId="net"
              orientation="right"
              stroke="#666"
              fontSize={12}
              tickFormatter={(v) => `${v}MB`}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: 'white',
                border: '1px solid #ccc',
                borderRadius: '8px'
              }}
              formatter={(value: number, name: string) =>
                name === 'network' ? [`${value}MB`, 'Network'] : [`${value}%`, name]
              }
            />
            {SERIES.map((s) => (
              <Area
                key={s.key}
                type="monotone"
                dataKey={s.key}
                yAxisId={s.axis === 'pct' ? 'pct' : 'net'}
                stroke={s.color}
                fill={s.color}
                fillOpacity={0.3}
                hide={!visible[s.key]}
              />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      </div>

      <div className="flex justify-center flex-wrap gap-x-6 gap-y-2 text-sm">
        {SERIES.map((s) => (
          <button
            key={s.key}
            type="button"
            onClick={() => toggle(s.key)}
            className="flex items-center transition-opacity"
            style={{ opacity: visible[s.key] ? 1 : 0.35 }}
          >
            <div className="w-3 h-3 rounded mr-2" style={{ backgroundColor: s.color }}></div>
            <span className="text-gray-600">{s.label}</span>
          </button>
        ))}
      </div>
    </div>
  );
}