import React, { useEffect, useState } from 'react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, LineChart, Line } from 'recharts';
import { useAgentStore } from '@/stores/agentStore';

export function AgentPerformanceChart() {
  const { agents, fetchAgents, fetchScorecards } = useAgentStore();
  const [analytics, setAnalytics] = useState<{ date: string; confidence: number }[]>([]);
  const [, setLoading] = useState<boolean>(true);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      try {
        await Promise.all([fetchAgents(), fetchScorecards()]);
        const res = await fetch('/api/reasoning/analytics?timeframe=24h');
        if (res.ok) {
          const payload = await res.json();
          const trend = payload.confidence_trend || payload.data?.confidence_trend || [];
          // normalize to chart format
          setAnalytics(trend.map((t: { date: string; confidence: number }) => ({ date: t.date, confidence: t.confidence * 100 })));
        }
      } catch (err) {
        console.error('Failed to load agent analytics', err);
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [fetchAgents, fetchScorecards]);

  const prepareAgentMetrics = () => {
    return agents.map((agent) => ({
      name: agent.name,
      accuracy: Math.round((agent.performance?.accuracy || 0) * 100),
      confidence: Math.round((agent.performance?.average_confidence || 0) * 100),
      signals: agent.performance?.total_signals || 0,
      successful: agent.performance?.successful_signals || 0,
    }));
  };

  const prepareAccuracyTrend = () => {
    // analytics is a list of {date, confidence}
    return analytics.map((a) => ({ time: a.date, avg_confidence: Math.round(a.confidence) }));
  };

  const metrics = prepareAgentMetrics();
  const trend = prepareAccuracyTrend();

  return (
    <div className="space-y-6">
      {/* Accuracy Overview */}
      <div>
        <h4 className="text-lg font-semibold text-gray-900 mb-4">Agent Accuracy Comparison</h4>
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={metrics.length ? metrics : [{ name: 'None', accuracy: 0, signals: 0 }] }>
              <CartesianGrid strokeDasharray="3 3" stroke="#e0e0e0" />
              <XAxis dataKey="name" stroke="#666" fontSize={12} />
              <YAxis stroke="#666" fontSize={12} />
              <Tooltip 
                contentStyle={{
                  backgroundColor: 'white',
                  border: '1px solid #ccc',
                  borderRadius: '8px'
                }}
              />
              <Bar dataKey="accuracy" fill="#3b82f6" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Accuracy Trend */}
      <div>
        <h4 className="text-lg font-semibold text-gray-900 mb-4">Accuracy Trend (Today)</h4>
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={trend.length ? trend : [{ time: 'N/A', avg_confidence: 0 }] }>
              <CartesianGrid strokeDasharray="3 3" stroke="#e0e0e0" />
              <XAxis dataKey="time" stroke="#666" fontSize={12} />
              <YAxis stroke="#666" fontSize={12} domain={[70, 95]} />
              <Tooltip 
                contentStyle={{
                  backgroundColor: 'white',
                  border: '1px solid #ccc',
                  borderRadius: '8px'
                }}
              />
              <Line type="monotone" dataKey="avg_confidence" stroke="#3b82f6" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div className="flex flex-wrap justify-center gap-4 mt-4 text-sm">
          <div className="flex items-center">
            <div className="w-3 h-3 bg-red-500 rounded mr-2"></div>
            <span className="text-gray-600">Sentiment</span>
          </div>
          <div className="flex items-center">
            <div className="w-3 h-3 bg-blue-500 rounded mr-2"></div>
            <span className="text-gray-600">Technical</span>
          </div>
          <div className="flex items-center">
            <div className="w-3 h-3 bg-purple-500 rounded mr-2"></div>
            <span className="text-gray-600">Visual</span>
          </div>
          <div className="flex items-center">
            <div className="w-3 h-3 bg-green-500 rounded mr-2"></div>
            <span className="text-gray-600">Qabba</span>
          </div>
          <div className="flex items-center">
            <div className="w-3 h-3 bg-yellow-500 rounded mr-2"></div>
            <span className="text-gray-600">Decision</span>
          </div>
          <div className="flex items-center">
            <div className="w-3 h-3 bg-cyan-500 rounded mr-2"></div>
            <span className="text-gray-600">Risk</span>
          </div>
        </div>
      </div>

      {/* Performance Summary */}
      <div>
        <h4 className="text-lg font-semibold text-gray-900 mb-4">Performance Summary</h4>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
          {metrics.map((agent) => (
            <div key={agent.name} className="bg-gray-50 rounded-lg p-4">
              <div className="flex items-center justify-between mb-2">
                <span className="font-medium text-gray-900">{agent.name}</span>
                <span className={`text-sm font-semibold ${
                  agent.accuracy >= 85 ? 'text-green-600' : 
                  agent.accuracy >= 75 ? 'text-yellow-600' : 'text-red-600'
                }`}>
                  {agent.accuracy}%
                </span>
              </div>
              <div className="space-y-1 text-sm text-gray-600">
                <div>Signals: {agent.signals}</div>
                <div>Success: {agent.successful}</div>
                <div>Confidence: {agent.confidence}%</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}