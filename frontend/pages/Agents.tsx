import React, { useState, useEffect, useRef } from 'react';
import { LineChart, Line, BarChart, Bar, PieChart, Pie, Cell, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import { Brain, TrendingUp, Activity, Target, AlertCircle, RefreshCw } from 'lucide-react';
// import { useAuthStore } from '../stores/authStore';
import { useAgentStore, ReasoningEntry } from '../stores/agentStore';
import { useSystemStore } from '../stores/systemStore';
import { Card, CardHeader, CardTitle, CardContent } from '../components/ui/Card';
import { Button } from '../components/ui/Button';
import { Select } from '../components/ui/Select';
import { Badge } from '../components/ui/Badge';
import { Alert, AlertTitle, AlertDescription } from '../components/ui/Alert';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/Tabs';
import { Input } from '../components/ui/Input';
import { Switch } from '../components/ui/Switch';

const AGENT_COLORS = {
  sentiment: '#3b82f6',
  technical: '#10b981',
  visual: '#f59e0b',
  qabba: '#ef4444',
  decision: '#8b5cf6',
  risk: '#f97316'
};

const AGENT_DISPLAY_NAMES: Record<string, string> = {
  sentiment: 'Sentiment',
  technical: 'Technical',
  visual: 'Visual',
  qabba: 'QABBA',
  decision: 'Decision',
  risk: 'Risk',
};

export const Agents: React.FC = () => {
  // const { user } = useAuthStore();
  const { agents, reasoningLogs, socket, fetchAgents, fetchReasoningLogs, fetchScorecards } = useAgentStore();
  const { engineConfig, fetchEngineConfig, updateEngineConfig } = useSystemStore();
  
  const [selectedAgent, setSelectedAgent] = useState<string>('all');
  const [selectedTimeframe, setSelectedTimeframe] = useState<string>('24h');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [agentOutputs, setAgentOutputs] = useState<ReasoningEntry[]>([]);
  const [filteredOutputs, setFilteredOutputs] = useState<ReasoningEntry[]>([]);
  const [configDraft, setConfigDraft] = useState({ symbol: 'BTCUSDT', timeframe: '15m', enable_visual_agent: true, enable_sentiment_agent: true, paper_trading: true, allow_live_trading: false });
  const [isSavingConfig, setIsSavingConfig] = useState(false);
  const [selectedLogAgent, setSelectedLogAgent] = useState<string | null>(null);

  // Live terminal feed
  type FeedLine = { id: string; ts: string; agent: string; signal: string; confidence: number; reasoning: string; isSummary?: boolean };

  const interpretFeedLine = (line: FeedLine): string => {
    const raw = line.reasoning || '';
    const signal = line.signal;
    const conf = line.confidence;

    // Parse common technical indicators from the reasoning string
    const rsi  = parseFloat((raw.match(/RSI[=:]\s*([\d.]+)/i)       || [])[1] ?? 'NaN');
    const macd = parseFloat((raw.match(/MACD_hist[=:]\s*([+-]?[\d.]+)/i) || [])[1] ?? 'NaN');
    const atr  = parseFloat((raw.match(/ATR[=:]\s*([\d.]+)/i)       || [])[1] ?? 'NaN');
    const ema  = raw.match(/EMA[_\s]?(fast|slow|cross|bull|bear)/i);
    const bb   = raw.match(/BB[_\s]?(squeeze|expand|upper|lower|mid)/i);

    const parts: string[] = [];

    // RSI
    if (!isNaN(rsi)) {
      if (rsi < 20)       parts.push(`RSI at ${rsi.toFixed(0)} is extremely oversold — market is exhausted to the downside`);
      else if (rsi < 30)  parts.push(`RSI at ${rsi.toFixed(0)} is oversold — potential bounce zone`);
      else if (rsi > 80)  parts.push(`RSI at ${rsi.toFixed(0)} is extremely overbought — rally may be overextended`);
      else if (rsi > 70)  parts.push(`RSI at ${rsi.toFixed(0)} is overbought — momentum slowing`);
      else if (rsi > 55)  parts.push(`RSI at ${rsi.toFixed(0)} leans bullish`);
      else if (rsi < 45)  parts.push(`RSI at ${rsi.toFixed(0)} leans bearish`);
      else                parts.push(`RSI at ${rsi.toFixed(0)} is neutral`);
    }

    // MACD histogram
    if (!isNaN(macd)) {
      if (Math.abs(macd) < 0.0005) parts.push('MACD histogram is flat — momentum has stalled');
      else if (macd > 0)            parts.push(`MACD histogram positive — upward momentum building`);
      else                          parts.push(`MACD histogram negative — downward pressure`);
    }

    // ATR (volatility)
    if (!isNaN(atr)) {
      if (atr > 500)       parts.push(`extreme market volatility (ATR ${atr.toFixed(0)}) — risk is very high`);
      else if (atr > 200)  parts.push(`elevated volatility (ATR ${atr.toFixed(0)}) — wider stops needed`);
      else if (atr < 50)   parts.push(`low volatility (ATR ${atr.toFixed(0)}) — market is consolidating`);
    }

    // EMA context
    if (ema) {
      const ctx = ema[1].toLowerCase();
      if (ctx === 'bull' || ctx === 'fast') parts.push('EMAs are bullishly aligned');
      else if (ctx === 'bear' || ctx === 'slow') parts.push('EMAs are bearishly aligned');
      else if (ctx === 'cross') parts.push('EMA crossover detected');
    }

    // BB context
    if (bb) {
      const ctx = bb[1].toLowerCase();
      if (ctx === 'squeeze') parts.push('Bollinger Bands squeezing — breakout likely soon');
      else if (ctx === 'expand') parts.push('Bollinger Bands expanding — volatility rising');
    }

    // Conviction label
    const conviction = conf < 0.4 ? 'weak conviction' : conf < 0.65 ? 'moderate conviction' : 'high conviction';

    // Signal conclusion
    let conclusion = '';
    if (!signal || signal === '—') {
      conclusion = 'no clear signal';
    } else if (signal === 'HOLD' || signal === 'WAIT') {
      if (parts.some(p => p.includes('oversold')))
        conclusion = 'oversold but no reversal confirmed — waiting for a trigger before entering long';
      else if (parts.some(p => p.includes('stalled') || p.includes('flat')))
        conclusion = 'momentum has stalled in both directions — no edge to trade right now';
      else if (parts.some(p => p.includes('overbought')))
        conclusion = 'overbought but no breakdown signal — staying out until price action clarifies';
      else
        conclusion = 'mixed signals — no high-probability setup at this time';
    } else if (signal === 'BUY') {
      conclusion = 'conditions align for a long entry';
    } else if (signal === 'SELL') {
      conclusion = 'conditions favor a short or exit from longs';
    }

    if (parts.length === 0) {
      // No recognizable indicators — show cleaned text
      const cleaned = raw.replace(/[A-Z_]+=[\d.+-]+\s*[|→]*\s*/g, '').trim();
      return cleaned.slice(0, 200) || conclusion;
    }

    return `${parts.join('; ')}. ${conclusion} (${conviction}).`;
  };
  const [feedLines, setFeedLines] = useState<FeedLine[]>([]);
  const feedRef = useRef<HTMLDivElement>(null);
  const [nextCycleAt, setNextCycleAt] = useState<Date | null>(null);
  const [countdown, setCountdown] = useState<string>('');

  useEffect(() => {
    fetchAgentsData();
    fetchEngineConfig();

    const handleCycleSummary = (data: { next_cycle_at?: string }) => {
      if (data?.next_cycle_at) setNextCycleAt(new Date(data.next_cycle_at));
    };

    if (socket) {
      socket.on('agentOutput', handleAgentOutput);
      socket.on('agent:reasoning', handleReasoningUpdate);
      socket.on('agent:scorecard', handleAgentUpdate);
      socket.on('cycle:summary', handleCycleSummary);
    }

    return () => {
      if (socket) {
        socket.off('agentOutput', handleAgentOutput);
        socket.off('agent:reasoning', handleReasoningUpdate);
        socket.off('agent:scorecard', handleAgentUpdate);
        socket.off('cycle:summary', handleCycleSummary);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [socket, selectedAgent, selectedTimeframe]);

  // Countdown ticker
  useEffect(() => {
    const tick = () => {
      if (!nextCycleAt) { setCountdown(''); return; }
      const diffMs = nextCycleAt.getTime() - Date.now();
      if (diffMs <= 0) { setCountdown('launching…'); return; }
      const m = Math.floor(diffMs / 60000);
      const s = Math.floor((diffMs % 60000) / 1000);
      setCountdown(`${m}:${String(s).padStart(2, '0')}`);
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [nextCycleAt]);

  useEffect(() => {
    if (engineConfig) {
      setConfigDraft({
        symbol: engineConfig.symbol,
        timeframe: engineConfig.timeframe,
        enable_visual_agent: engineConfig.enable_visual_agent,
        enable_sentiment_agent: engineConfig.enable_sentiment_agent,
        paper_trading: engineConfig.paper_trading,
        allow_live_trading: engineConfig.allow_live_trading,
      });
    }
  }, [engineConfig]);

  const fetchAgentsData = async () => {
    try {
      setLoading(true);
      setError(null);

      await Promise.all([
        fetchAgents(),
        fetchReasoningLogs({ timeframe: selectedTimeframe })
      ]);

      // Fetch recent agent outputs
      const response = await fetch(`/api/agents/outputs?timeframe=${selectedTimeframe}`);
      if (response.ok) {
        const outputs = await response.json();
        const payload = outputs.data || outputs.outputs || outputs;
        setAgentOutputs(payload);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch agents data');
    } finally {
      setLoading(false);
    }
  };

  const handleAgentUpdate = () => {
    // Scorecard update — refresh agents and scorecards from store
    fetchAgents();
    fetchScorecards();
  };

  const handleReasoningUpdate = (entry: ReasoningEntry) => {
    // New reasoning log — add to local list
    setAgentOutputs(prev => [entry, ...prev.slice(0, 99)]);
  };

  const handleAgentOutput = (output: ReasoningEntry) => {
    setAgentOutputs(prev => [output, ...prev]);
    // Also push to live terminal feed
    const isSummary = (output.agent_name || '').includes('Summary');
    const line: FeedLine = {
      id: output.id || crypto.randomUUID(),
      ts: new Date(output.timestamp || Date.now()).toLocaleTimeString(),
      agent: output.agent_name || 'Agent',
      signal: output.decision || '—',
      confidence: output.confidence ?? 0,
      reasoning: (output.reasoning || '').slice(0, 300),
      isSummary,
    };
    setFeedLines(prev => [...prev.slice(-199), line]);
  };

  // Auto-scroll terminal to bottom when new lines arrive
  useEffect(() => {
    if (feedRef.current) {
      feedRef.current.scrollTop = feedRef.current.scrollHeight;
    }
  }, [feedLines]);

  useEffect(() => {
    // Filter outputs based on selected agent
    if (selectedAgent === 'all') {
      setFilteredOutputs(agentOutputs);
    } else {
      setFilteredOutputs(agentOutputs.filter(output => output.agent_id === selectedAgent));
    }
  }, [agentOutputs, selectedAgent]);

  const handleSaveConfig = async () => {
    try {
      setIsSavingConfig(true);
      await updateEngineConfig(configDraft);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update config');
    } finally {
      setIsSavingConfig(false);
    }
  };

  const getAgentStatusColor = (status: string) => {
    switch (status) {
      case 'active': return 'success';
      case 'error': return 'error';
      default: return 'default';
    }
  };

  const getConfidenceColor = (confidence: number) => {
    if (confidence >= 0.8) return 'text-green-600';
    if (confidence >= 0.6) return 'text-yellow-600';
    return 'text-red-600';
  };

  const getAccuracyColor = (accuracy: number) => {
    if (accuracy >= 0.8) return 'text-green-600';
    if (accuracy >= 0.6) return 'text-yellow-600';
    return 'text-red-600';
  };

  const prepareAgentMetrics = () => {
    const metrics = agents.map(agent => ({
      name: agent.name,
      executions: agent.performance.total_signals,
      accuracy: agent.performance.accuracy * 100,
      confidence: agent.performance.average_confidence * 100,
      successRate: (agent.performance.successful_signals / Math.max(agent.performance.total_signals, 1)) * 100,
      color: AGENT_COLORS[agent.type as keyof typeof AGENT_COLORS]
    }));

    return metrics;
  };

  const prepareAgentDistribution = () => {
    const distribution = agents.reduce((acc, agent) => {
      const type = agent.type;
      acc[type] = (acc[type] || 0) + 1;
      return acc;
    }, {} as Record<string, number>);

    return Object.entries(distribution).map(([type, count]) => ({
      name: type.charAt(0).toUpperCase() + type.slice(1),
      value: count,
      color: AGENT_COLORS[type as keyof typeof AGENT_COLORS]
    }));
  };

  const prepareConfidenceTimeline = () => {
    // Group reasoning logs by date and calculate average confidence
    const timeline = reasoningLogs.reduce((acc, log) => {
      const date = new Date(log.timestamp).toLocaleDateString();
      if (!acc[date]) {
        acc[date] = { date, confidence: 0, count: 0 };
      }
      acc[date].confidence += log.confidence;
      acc[date].count += 1;
      return acc;
    }, {} as Record<string, { date: string; confidence: number; count: number }>);

    return Object.values(timeline).map(entry => ({
      date: entry.date,
      confidence: entry.count > 0 ? entry.confidence / entry.count : 0
    })).sort((a, b) => new Date(a.date).getTime() - new Date(b.date).getTime());
  };

  const renderAgentOutput = (output: ReasoningEntry) => {
    const renderContent = () => {
      return (
        <div className="bg-gray-50 rounded-lg p-3">
          <div className="text-xs">
            <div><strong>Input:</strong> {JSON.stringify(output.input_data, null, 2)}</div>
            <div className="mt-2"><strong>Reasoning:</strong> {output.reasoning}</div>
            <div className="mt-2"><strong>Decision:</strong> {output.decision}</div>
            {output.outcome && (
              <div className="mt-2">
                <strong>Outcome:</strong> {output.outcome.accuracy}% accuracy
                (Actual: ${output.outcome.actual_price}, Predicted: ${output.outcome.predicted_price})
              </div>
            )}
          </div>
        </div>
      );
    };

    return (
      <div key={output.id} className="border rounded-lg p-4 mb-4">
        <div className="flex justify-between items-start mb-3">
          <div>
            <h4 className="font-semibold text-lg">{output.agent_name}</h4>
            <p className="text-sm text-gray-500">
              {new Date(output.timestamp).toLocaleString()}
            </p>
          </div>
          <div className="flex items-center space-x-2">
            <span className={`font-medium ${getConfidenceColor(output.confidence)}`}>
              {(output.confidence * 100).toFixed(1)}% confidence
            </span>
            <Badge variant={output.confidence >= 0.8 ? 'success' : output.confidence >= 0.6 ? 'warning' : 'error'}>
              {output.confidence >= 0.8 ? 'High' : output.confidence >= 0.6 ? 'Medium' : 'Low'}
            </Badge>
          </div>
        </div>

        {!!output.input_data && typeof output.input_data === 'object' && Object.keys(output.input_data).length > 0 && (
          <div className="mb-3">
            <h5 className="text-sm font-medium text-gray-700 mb-1">Input Data:</h5>
            <div className="flex flex-wrap gap-2">
              {Object.entries(output.input_data as Record<string, unknown>).slice(0, 5).map(([key, value]) => (
                <Badge key={key} variant="outline" className="text-xs">
                  {key}: {String(value)}
                </Badge>
              ))}
            </div>
          </div>
        )}

        <div className="mb-3">
          <h5 className="text-sm font-medium text-gray-700 mb-1">Output:</h5>
          {renderContent()}
        </div>
      </div>
    );
  };

  const renderReasoningEntry = (entry: ReasoningEntry) => {
    return (
      <div key={entry.id} className="border rounded-lg p-4 mb-4">
        <div className="flex justify-between items-start mb-3">
          <div>
            <h4 className="font-semibold text-lg">{entry.agent_name}</h4>
            <p className="text-sm text-gray-500">
              {new Date(entry.timestamp).toLocaleString()}
            </p>
          </div>
          <div className="flex items-center space-x-2">
            <span className={`font-medium ${getConfidenceColor(entry.confidence)}`}>
              {(entry.confidence * 100).toFixed(1)}% confidence
            </span>
            <Badge variant={entry.confidence >= 0.8 ? 'success' : entry.confidence >= 0.6 ? 'warning' : 'error'}>
              {entry.confidence >= 0.8 ? 'High' : entry.confidence >= 0.6 ? 'Medium' : 'Low'}
            </Badge>
          </div>
        </div>

        {!!entry.input_data && typeof entry.input_data === 'object' && (
          <div className="mb-3">
            <h5 className="text-sm font-medium text-gray-700 mb-1">Input Data:</h5>
            <div className="bg-gray-50 rounded-lg p-3">
              <pre className="text-xs overflow-x-auto">
                {JSON.stringify(entry.input_data, null, 2)}
              </pre>
            </div>
          </div>
        )}

        <div className="mb-3">
          <h5 className="text-sm font-medium text-gray-700 mb-1">Reasoning:</h5>
          <p className="text-sm bg-blue-50 rounded-lg p-3">{entry.reasoning}</p>
        </div>

        <div className="mb-3">
          <h5 className="text-sm font-medium text-gray-700 mb-1">Decision:</h5>
          <p className="text-sm bg-green-50 rounded-lg p-3">{entry.decision}</p>
        </div>

        {entry.outcome && (
          <div className="mb-3">
            <h5 className="text-sm font-medium text-gray-700 mb-1">Outcome:</h5>
            <div className="bg-yellow-50 rounded-lg p-3">
              <div className="flex justify-between items-start mb-2">
                <span className="font-medium">Accuracy: {entry.outcome.accuracy}%</span>
                <Badge variant={entry.outcome.accuracy > 70 ? 'success' : 'error'}>
                  {entry.outcome.accuracy > 70 ? 'Success' : 'Error'}
                </Badge>
              </div>
              <p className="text-sm">Actual: ${entry.outcome.actual_price}, Predicted: ${entry.outcome.predicted_price}</p>
              <p className="text-sm mt-1">{entry.outcome.judge_feedback}</p>
            </div>
          </div>
        )}

        {entry.outcome && (
          <div className="flex justify-end">
            <Badge variant={entry.outcome.accuracy > 70 ? 'success' : 'error'}>
              Accuracy: {entry.outcome.accuracy}%
            </Badge>
          </div>
        )}
      </div>
    );
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {error && (
        <Alert variant="error" className="mb-4">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>Error</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* Live Control Panel */}
      <Card>
        <CardHeader className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
          <div>
            <CardTitle>Live Control Panel</CardTitle>
            <p className="text-sm text-gray-500">Change symbol, timeframe and active agents. Engine restarts automatically.</p>
          </div>
          <div className="flex items-center gap-3">
            <Badge variant={configDraft.paper_trading ? 'success' : 'warning'}>
              {configDraft.paper_trading ? 'Paper trading' : 'Live ready'}
            </Badge>
            <Badge variant={configDraft.allow_live_trading ? 'error' : 'default'}>
              {configDraft.allow_live_trading ? 'Live enabled' : 'Live blocked'}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Symbol</label>
              <Input
                value={configDraft.symbol}
                onChange={(e) => setConfigDraft(prev => ({ ...prev, symbol: e.target.value.toUpperCase() }))}
                placeholder="BTCUSDT"
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Timeframe</label>
              <Select
                value={configDraft.timeframe}
                onChange={(e) => setConfigDraft(prev => ({ ...prev, timeframe: e.target.value }))}
              >
                {['1m','5m','15m','1h','4h'].map(tf => (
                  <option key={tf} value={tf}>{tf}</option>
                ))}
              </Select>
            </div>
            <div className="flex items-center justify-between bg-gray-50 rounded-lg px-3 py-2">
              <div>
                <p className="text-sm font-medium">Visual Agent</p>
                <p className="text-xs text-gray-500">Pattern recognition</p>
              </div>
              <Switch
                checked={configDraft.enable_visual_agent}
                onChange={(val) => setConfigDraft(prev => ({ ...prev, enable_visual_agent: val }))}
              />
            </div>
            <div className="flex items-center justify-between bg-gray-50 rounded-lg px-3 py-2">
              <div>
                <p className="text-sm font-medium">Sentiment Agent</p>
                <p className="text-xs text-gray-500">News & social media</p>
              </div>
              <Switch
                checked={configDraft.enable_sentiment_agent}
                onChange={(val) => setConfigDraft(prev => ({ ...prev, enable_sentiment_agent: val }))}
              />
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div className="flex items-center justify-between bg-gray-50 rounded-lg px-3 py-2">
              <div>
                <p className="text-sm font-medium">Paper Trading</p>
                <p className="text-xs text-gray-500">Simulated trades</p>
              </div>
              <Switch
                checked={configDraft.paper_trading}
                onChange={(val) => setConfigDraft(prev => ({ ...prev, paper_trading: val }))}
              />
            </div>
            <div className="flex items-center justify-between bg-gray-50 rounded-lg px-3 py-2">
              <div>
                <p className="text-sm font-medium">Allow Live</p>
                <p className="text-xs text-gray-500">Requires manual confirmation</p>
              </div>
              <Switch
                checked={configDraft.allow_live_trading}
                onChange={(val) => setConfigDraft(prev => ({ ...prev, allow_live_trading: val }))}
              />
            </div>
            <div className="flex items-center justify-end gap-3">
              <Button variant="outline" onClick={() => fetchEngineConfig()}>Reset</Button>
              <Button onClick={handleSaveConfig} disabled={isSavingConfig} className="bg-indigo-600 hover:bg-indigo-700">
                {isSavingConfig ? 'Applying...' : 'Apply & restart'}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Agent Overview */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Active Agents</CardTitle>
            <Brain className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {agents.filter(a => a.status === 'active').length}
            </div>
            <p className="text-xs text-muted-foreground">
              {agents.length} total agents
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Avg Confidence</CardTitle>
            <TrendingUp className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {agents.length > 0 
                ? (agents.reduce((sum, a) => sum + a.performance.average_confidence, 0) / agents.length * 100).toFixed(1)
                : 0
              }%
            </div>
            <p className="text-xs text-muted-foreground">
              Across all agents
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Avg Accuracy</CardTitle>
            <Target className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {agents.length > 0 
                ? (agents.reduce((sum, a) => sum + a.performance.accuracy, 0) / agents.length * 100).toFixed(1)
                : 0
              }%
            </div>
            <p className="text-xs text-muted-foreground">
              Last 30 days
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Total Executions</CardTitle>
            <Activity className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {agents.reduce((sum, a) => sum + a.performance.total_signals, 0)}
            </div>
            <p className="text-xs text-muted-foreground">
              All time executions
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Filters */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between">
            <span>Agent Analysis</span>
            <div className="flex items-center space-x-2">
              <Button
                variant="outline"
                size="sm"
                onClick={fetchAgentsData}
                className="flex items-center space-x-1"
              >
                <RefreshCw className="h-4 w-4" />
                <span>Refresh</span>
              </Button>
            </div>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Agent</label>
              <Select
                value={selectedAgent}
                onChange={(e) => setSelectedAgent(e.target.value)}
              >
                <option value="all">All Agents</option>
                {agents.map(agent => (
                  <option key={agent.id} value={agent.id}>
                    {agent.name} ({agent.type})
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Timeframe</label>
              <Select
                value={selectedTimeframe}
                onChange={(e) => setSelectedTimeframe(e.target.value)}
              >
                <option value="1h">Last Hour</option>
                <option value="24h">Last 24 Hours</option>
                <option value="7d">Last 7 Days</option>
                <option value="30d">Last 30 Days</option>
              </Select>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card>
          <CardHeader>
            <CardTitle>Agent Performance Metrics</CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={prepareAgentMetrics()}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="name" />
                <YAxis />
                <Tooltip />
                <Legend />
                <Bar dataKey="accuracy" fill="#3b82f6" name="Accuracy %" />
                <Bar dataKey="confidence" fill="#10b981" name="Confidence %" />
                <Bar dataKey="successRate" fill="#f59e0b" name="Success Rate %" />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Agent Distribution</CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={300}>
              <PieChart>
                <Pie
                  data={prepareAgentDistribution()}
                  cx="50%"
                  cy="50%"
                  labelLine={false}
                  label={({ name, value }) => `${name}: ${value}`}
                  outerRadius={80}
                  fill="#8884d8"
                  dataKey="value"
                >
                  {prepareAgentDistribution().map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={entry.color} />
                  ))}
                </Pie>
                <Tooltip />
              </PieChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </div>

      {/* Confidence Timeline */}
      <Card>
        <CardHeader>
          <CardTitle>Confidence Timeline</CardTitle>
        </CardHeader>
        <CardContent>
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={prepareConfidenceTimeline()}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="date" />
              <YAxis />
              <Tooltip />
              <Legend />
              <Line 
                type="monotone" 
                dataKey="confidence" 
                stroke="#3b82f6" 
                strokeWidth={2}
                name="Average Confidence"
              />
            </LineChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>

      {/* Agent Outputs and Reasoning */}
      <Tabs defaultValue="live" className="space-y-4">
        <TabsList>
          <TabsTrigger value="live">Live Feed</TabsTrigger>
          <TabsTrigger value="outputs">Agent Outputs</TabsTrigger>
          <TabsTrigger value="reasoning">Reasoning Bank</TabsTrigger>
          <TabsTrigger value="agents">Agent Status</TabsTrigger>
        </TabsList>

        <TabsContent value="live">
          {/* 6 Agent Boxes */}
          <div className="grid grid-cols-3 md:grid-cols-6 gap-2 mb-3">
            {(Object.entries(AGENT_COLORS) as [string, string][]).map(([type, color]) => {
              const lastLine = [...feedLines].reverse().find(l =>
                l.agent.toLowerCase().includes(type)
              );
              const agentRecord = agents.find(a => a.type === type);
              const sigColor =
                lastLine?.signal === 'BUY' ? 'text-green-400' :
                lastLine?.signal === 'SELL' ? 'text-red-400' : 'text-yellow-400';
              return (
                <button
                  key={type}
                  onClick={() => setSelectedLogAgent(type)}
                  className="relative rounded-lg border border-gray-700 bg-gray-900 p-2 text-left hover:border-gray-500 transition-all focus:outline-none"
                  style={{ borderLeftColor: color, borderLeftWidth: 3 }}
                  title={`View ${AGENT_DISPLAY_NAMES[type]} agent logs`}
                >
                  <div className="absolute top-1.5 right-1.5 w-2 h-2 rounded-full"
                    style={{ background: agentRecord?.status === 'active' ? '#4ade80' : '#6b7280' }}
                  />
                  <div className="text-xs font-semibold text-gray-200">{AGENT_DISPLAY_NAMES[type]}</div>
                  {lastLine ? (
                    <>
                      <div className={`text-xs font-bold mt-0.5 ${sigColor}`}>{lastLine.signal}</div>
                      <div className="text-[10px] text-gray-500">{(lastLine.confidence * 100).toFixed(0)}% conf</div>
                    </>
                  ) : (
                    <div className="text-[10px] text-gray-600 mt-0.5">no data yet</div>
                  )}
                </button>
              );
            })}
          </div>

          <Card className="bg-gray-950 border-gray-800">
            <CardHeader className="flex flex-row items-center justify-between pb-2">
              <CardTitle className="text-green-400 font-mono text-sm tracking-widest">
                ● AGENT LIVE FEED
              </CardTitle>
              <div className="flex items-center gap-4">
                {nextCycleAt && (
                  <span className="font-mono text-xs text-gray-400">
                    next cycle{' '}
                    <span className="text-cyan-400 font-bold">{countdown}</span>
                    <span className="text-gray-600 ml-1">
                      ({nextCycleAt.toLocaleTimeString()})
                    </span>
                  </span>
                )}
                <button
                  onClick={() => setFeedLines([])}
                  className="text-xs text-gray-500 hover:text-gray-300 font-mono"
                >
                  clear
                </button>
              </div>
            </CardHeader>
            <CardContent className="p-0">
              <div
                ref={feedRef}
                className="font-mono text-xs bg-gray-950 text-gray-200 h-96 overflow-y-auto px-4 py-2 space-y-1"
              >
                {feedLines.length === 0 ? (
                  <p className="text-gray-600 mt-4">
                    Waiting for agent events… (starts on next analysis cycle)
                  </p>
                ) : (
                  feedLines.map(line => {
                    if (line.isSummary) {
                      return (
                        <div key={line.id} className="border-t border-gray-700 mt-2 pt-2 mb-2 leading-5">
                          <span className="text-gray-500">[{line.ts}]</span>{' '}
                          <span className="text-purple-400 font-bold">{line.agent}</span>
                          <div className="text-gray-300 pl-4 text-[11px] mt-0.5 italic">
                            ↳ {interpretFeedLine(line)}
                          </div>
                        </div>
                      );
                    }
                    const sigColor =
                      line.signal === 'BUY'
                        ? 'text-green-400'
                        : line.signal === 'SELL'
                        ? 'text-red-400'
                        : 'text-yellow-400';
                    const confPct = (line.confidence * 100).toFixed(0);
                    return (
                      <div key={line.id} className="leading-5 mb-1">
                        <div>
                          <span className="text-gray-500">[{line.ts}]</span>{' '}
                          <span className="text-blue-400">{line.agent}</span>{' '}
                          <span className="text-gray-400">→</span>{' '}
                          <span className={`font-bold ${sigColor}`}>{line.signal}</span>{' '}
                          <span className="text-gray-500">({confPct}%)</span>
                        </div>
                        <div className="text-gray-400 pl-4 text-[11px] italic">
                          ↳ {interpretFeedLine(line)}
                        </div>
                      </div>
                    );
                  })
                )}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="outputs" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Recent Agent Outputs</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                {filteredOutputs.length > 0 ? (
                  filteredOutputs.slice(0, 10).map(renderAgentOutput)
                ) : (
                  <div className="text-center py-8 text-gray-500">
                    No agent outputs found
                  </div>
                )}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="reasoning" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Reasoning Bank Entries</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                {reasoningLogs.length > 0 ? (
                  reasoningLogs.slice(0, 10).map(renderReasoningEntry)
                ) : (
                  <div className="text-center py-8 text-gray-500">
                    No reasoning entries found
                  </div>
                )}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="agents" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Agent Status</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                        Agent
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                        Type
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                        Status
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                        Confidence
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                        Accuracy
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                        Executions
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                        Last Execution
                      </th>
                    </tr>
                  </thead>
                  <tbody className="bg-white divide-y divide-gray-200">
                    {agents.map((agent) => (
                      <tr key={agent.id}>
                        <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">
                          {agent.name}
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                          <Badge 
                            variant="outline" 
                            className={`capitalize border-2`}
                          >
                            {agent.type}
                          </Badge>
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                          <Badge variant={getAgentStatusColor(agent.status)}>
                            {agent.status}
                          </Badge>
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                          <span className={getConfidenceColor(agent.performance.average_confidence)}>
                            {(agent.performance.average_confidence * 100).toFixed(1)}%
                          </span>
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                          <span className={getAccuracyColor(agent.performance.accuracy)}>
                            {(agent.performance.accuracy * 100).toFixed(1)}%
                          </span>
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                          {agent.performance.total_signals}
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                          {new Date(agent.last_run).toLocaleString()}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {agents.length === 0 && (
                  <div className="text-center py-8 text-gray-500">
                    No agents found
                  </div>
                )}
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {/* Agent Log Modal */}
      {selectedLogAgent && (() => {
        const color = AGENT_COLORS[selectedLogAgent as keyof typeof AGENT_COLORS];
        const label = AGENT_DISPLAY_NAMES[selectedLogAgent] || selectedLogAgent;

        const liveLogs = [...feedLines]
          .filter(l => l.agent.toLowerCase().includes(selectedLogAgent))
          .reverse();

        const storedLogs = agentOutputs
          .filter(o =>
            (o.agent_name || '').toLowerCase().includes(selectedLogAgent) ||
            (o.agent_id || '').toLowerCase().includes(selectedLogAgent)
          )
          .slice(0, 30);

        const hasLive = liveLogs.length > 0;
        const hasStored = storedLogs.length > 0;

        return (
          <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
            onClick={() => setSelectedLogAgent(null)}
          >
            <div
              className="bg-gray-950 border border-gray-700 rounded-xl w-full max-w-2xl max-h-[80vh] flex flex-col"
              onClick={e => e.stopPropagation()}
            >
              {/* Header */}
              <div className="flex items-center justify-between px-4 py-3 border-b border-gray-800 shrink-0">
                <div className="flex items-center gap-2">
                  <div className="w-3 h-3 rounded-full" style={{ background: color }} />
                  <span className="text-white font-semibold font-mono">{label} Agent — Logs</span>
                </div>
                <button
                  onClick={() => setSelectedLogAgent(null)}
                  className="text-gray-400 hover:text-white text-lg leading-none"
                >
                  ✕
                </button>
              </div>

              {/* Body */}
              <div className="overflow-y-auto flex-1 px-4 py-3 font-mono text-xs space-y-3">
                {!hasLive && !hasStored && (
                  <p className="text-gray-600 text-center py-8">No logs yet for this agent.</p>
                )}

                {hasLive && (
                  <div>
                    <div className="text-gray-500 text-[10px] uppercase tracking-widest mb-2">Live feed</div>
                    <div className="space-y-2">
                      {liveLogs.map(line => {
                        const sigColor =
                          line.signal === 'BUY' ? 'text-green-400' :
                          line.signal === 'SELL' ? 'text-red-400' : 'text-yellow-400';
                        return (
                          <div key={line.id} className="border-b border-gray-800 pb-2">
                            <div>
                              <span className="text-gray-500">[{line.ts}]</span>{' '}
                              <span className={`font-bold ${sigColor}`}>{line.signal}</span>{' '}
                              <span className="text-gray-500">({(line.confidence * 100).toFixed(0)}%)</span>
                            </div>
                            <div className="text-gray-400 mt-0.5 italic pl-3">↳ {line.reasoning}</div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                {hasStored && (
                  <div>
                    <div className="text-gray-500 text-[10px] uppercase tracking-widest mb-2">Stored outputs</div>
                    <div className="space-y-2">
                      {storedLogs.map(o => {
                        const sigColor =
                          o.decision === 'BUY' ? 'text-green-400' :
                          o.decision === 'SELL' ? 'text-red-400' : 'text-yellow-400';
                        return (
                          <div key={o.id} className="border-b border-gray-800 pb-2">
                            <div>
                              <span className="text-gray-500">[{new Date(o.timestamp).toLocaleTimeString()}]</span>{' '}
                              <span className={`font-bold ${sigColor}`}>{o.decision || '—'}</span>{' '}
                              <span className="text-gray-500">({((o.confidence || 0) * 100).toFixed(0)}%)</span>
                            </div>
                            {o.reasoning && (
                              <div className="text-gray-400 mt-0.5 italic pl-3">↳ {o.reasoning.slice(0, 400)}</div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        );
      })()}
    </div>
  );
};
