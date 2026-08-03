import React, { useEffect, useState } from 'react';
import { XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Area, AreaChart } from 'recharts';
import { useSystemStore } from '@/stores/systemStore';
import { authHeaders } from '@/lib/auth';

interface MetricsHistoryPoint {
  timestamp: number;
  cpu: { usage: number };
  memory: { percentage: number };
  disk: { percentage: number };
  network: { bytes_in: number; bytes_out: number };
}

export function SystemStatusChart() {
  const { metrics } = useSystemStore();
  const [history, setHistory] = useState<MetricsHistoryPoint[]>([]);

  useEffect(() => {
    let active = true;
    const loadHistory = async () => {
      try {
        const response = await fetch('/api/system/metrics/history?timeframe=1h', {
          headers: authHeaders(),
        });
        if (!response.ok) return;
        const payload = await response.json();
        if (active) setHistory(Array.isArray(payload.metrics) ? payload.metrics : []);
      } catch {
        // Keep the last real samples while the API is temporarily unavailable.
      }
    };
    void loadHistory();
    const interval = window.setInterval(loadHistory, 30_000);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, []);

  const systemSeries = history.map((point) => ({
    time: new Date(point.timestamp * 1000).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    }),
    cpu: point.cpu.usage,
    memory: point.memory.percentage,
    disk: point.disk.percentage,
    network: (point.network.bytes_in + point.network.bytes_out) / (1024 * 1024),
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
        {systemSeries.length > 0 ? (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={systemSeries}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e0e0e0" />
            <XAxis dataKey="time" stroke="#666" fontSize={12} />
            <YAxis stroke="#666" fontSize={12} />
            <Tooltip 
              contentStyle={{
                backgroundColor: 'white',
                border: '1px solid #ccc',
                borderRadius: '8px'
              }}
            />
            <Area
              type="monotone"
              dataKey="cpu"
              stroke="#3b82f6"
              fill="#3b82f6"
              fillOpacity={0.3}
            />
            <Area
              type="monotone"
              dataKey="memory"
              stroke="#10b981"
              fill="#10b981"
              fillOpacity={0.3}
            />
            <Area
              type="monotone"
              dataKey="disk"
              stroke="#8b5cf6"
              fill="#8b5cf6"
              fillOpacity={0.3}
            />
            <Area
              type="monotone"
              dataKey="network"
              stroke="#f59e0b"
              fill="#f59e0b"
              fillOpacity={0.3}
            />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <div className="h-full flex items-center justify-center text-sm text-gray-500">
            Metric history will appear after the first real samples are collected.
          </div>
        )}
      </div>

      <div className="flex justify-center space-x-6 text-sm">
        <div className="flex items-center">
          <div className="w-3 h-3 bg-blue-500 rounded mr-2"></div>
          <span className="text-gray-600">CPU</span>
        </div>
        <div className="flex items-center">
          <div className="w-3 h-3 bg-green-500 rounded mr-2"></div>
          <span className="text-gray-600">Memory</span>
        </div>
        <div className="flex items-center">
          <div className="w-3 h-3 bg-purple-500 rounded mr-2"></div>
          <span className="text-gray-600">Disk</span>
        </div>
        <div className="flex items-center">
          <div className="w-3 h-3 bg-orange-500 rounded mr-2"></div>
          <span className="text-gray-600">Network</span>
        </div>
      </div>
    </div>
  );
}
