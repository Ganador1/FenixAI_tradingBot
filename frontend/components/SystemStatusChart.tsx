import React from 'react';
import { XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Area, AreaChart } from 'recharts';
import { useSystemStore } from '@/stores/systemStore';

export function SystemStatusChart() {
  const { metrics } = useSystemStore();
  // Create a lightweight timeseries from the last metrics snapshot for chart
  const times = ['T-5', 'T-4', 'T-3', 'T-2', 'T-1', 'Now'];
  const baseline = metrics || { cpu: 50, memory: 60, disk: 40, network: 20 };
  const systemSeries = times.map((t, i) => ({
    time: t,
    cpu: Math.max(0, Math.round(baseline.cpu + (i - 3) * 2)),
    memory: Math.max(0, Math.round(baseline.memory + (i - 3) * 1.5)),
    disk: Math.max(0, Math.round(baseline.disk + (i - 3) * 1)),
    network: Math.max(0, Math.round((baseline.network || 0) + (i - 3) * 1)),
  }));

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <div className="text-center">
          <div className="text-2xl font-bold text-blue-600">62%</div>
          <div className="text-sm text-gray-600">CPU Usage</div>
        </div>
        <div className="text-center">
          <div className="text-2xl font-bold text-green-600">72%</div>
          <div className="text-sm text-gray-600">Memory</div>
        </div>
        <div className="text-center">
          <div className="text-2xl font-bold text-purple-600">45%</div>
          <div className="text-sm text-gray-600">Disk</div>
        </div>
        <div className="text-center">
          <div className="text-2xl font-bold text-orange-600">40%</div>
          <div className="text-sm text-gray-600">Network</div>
        </div>
      </div>

      <div className="h-64">
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
              stackId="1"
              stroke="#3b82f6"
              fill="#3b82f6"
              fillOpacity={0.3}
            />
            <Area
              type="monotone"
              dataKey="memory"
              stackId="1"
              stroke="#10b981"
              fill="#10b981"
              fillOpacity={0.3}
            />
            <Area
              type="monotone"
              dataKey="disk"
              stackId="1"
              stroke="#8b5cf6"
              fill="#8b5cf6"
              fillOpacity={0.3}
            />
            <Area
              type="monotone"
              dataKey="network"
              stackId="1"
              stroke="#f59e0b"
              fill="#f59e0b"
              fillOpacity={0.3}
            />
          </AreaChart>
        </ResponsiveContainer>
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