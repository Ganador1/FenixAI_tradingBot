import React, { useState, useEffect } from 'react';
import { LineChart, Line, BarChart, Bar, PieChart, Pie, Cell, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import { Brain, TrendingUp, Activity, Target, AlertCircle, RefreshCw } from 'lucide-react';
// import { useAuthStore } from '../stores/authStore';
import { useAgentStore, Agent, ReasoningEntry } from '../stores/agentStore';
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

export const Agents: React.FC = () => {
  // const { user } = useAuthStore();
  const { agents, reasoningLogs, socket, fetchAgents, fetchReasoningLogs } = useAgentStore();
  const { engineConfig, fetchEngineConfig, updateEngineConfig } = useSystemStore();
  
  const [selectedAgent, setSelectedAgent] = useState<string>('all');
  const [selectedTimeframe, setSelectedTimeframe] = useState<string>('24h');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [agentOutputs, setAgentOutputs] = useState<ReasoningEntry[]>([]);
  const [filteredOutputs, setFilteredOutputs] = useState<ReasoningEntry[]>([]);
  const [configDraft, setConfigDraft] = useState({ symbol: 'BTCUSDT', timeframe: '15m', enable_visual_agent: true, enable_sentiment_agent: true, paper_trading: true, allow_live_trading: false });
  const [isSavingConfig, setIsSavingConfig] = useState(false);

  useEffect(() => {
    fetchAgentsData();
    fetchEngineConfig();
    
    if (socket) {
      socket.on('agentUpdate', handleAgentUpdate);
      socket.on('reasoningUpdate', handleReasoningUpdate);
      socket.on('agentOutput', handleAgentOutput);
    }

    return () => {
      if (socket) {
        socket.off('agentUpdate', handleAgentUpdate);
        socket.off('reasoningUpdate', handleReasoningUpdate);
        socket.off('agentOutput', handleAgentOutput);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [socket, selectedAgent, selectedTimeframe]);

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

  const handleAgentUpdate = (agent: Agent) => {
    // This would be handled by the agent store
    console.log('Agent update:', agent);
  };

  const handleReasoningUpdate = (entry: ReasoningEntry) => {
    // This would be handled by the agent store
    console.log('Reasoning update:', entry);
  };

  const handleAgentOutput = (output: ReasoningEntry) => {
    setAgentOutputs(prev => [output, ...prev]);
  };

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
            <CardTitle>Panel de Control en Vivo</CardTitle>
            <p className="text-sm text-gray-500">Cambia símbolo, timeframe y agentes activos. Reinicia el motor automáticamente.</p>
          </div>
          <div className="flex items-center gap-3">
            <Badge variant={configDraft.paper_trading ? 'success' : 'warning'}>
              {configDraft.paper_trading ? 'Paper trading' : 'Live ready'}
            </Badge>
            <Badge variant={configDraft.allow_live_trading ? 'error' : 'default'}>
              {configDraft.allow_live_trading ? 'Live permitido' : 'Live bloqueado'}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Símbolo</label>
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
                <p className="text-sm font-medium">Agente Visual</p>
                <p className="text-xs text-gray-500">Reconocimiento de patrones</p>
              </div>
              <Switch
                checked={configDraft.enable_visual_agent}
                onChange={(val) => setConfigDraft(prev => ({ ...prev, enable_visual_agent: val }))}
              />
            </div>
            <div className="flex items-center justify-between bg-gray-50 rounded-lg px-3 py-2">
              <div>
                <p className="text-sm font-medium">Agente Sentiment</p>
                <p className="text-xs text-gray-500">Noticias y redes</p>
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
                <p className="text-xs text-gray-500">Operaciones simuladas</p>
              </div>
              <Switch
                checked={configDraft.paper_trading}
                onChange={(val) => setConfigDraft(prev => ({ ...prev, paper_trading: val }))}
              />
            </div>
            <div className="flex items-center justify-between bg-gray-50 rounded-lg px-3 py-2">
              <div>
                <p className="text-sm font-medium">Permitir Live</p>
                <p className="text-xs text-gray-500">Requiere confirmación manual</p>
              </div>
              <Switch
                checked={configDraft.allow_live_trading}
                onChange={(val) => setConfigDraft(prev => ({ ...prev, allow_live_trading: val }))}
              />
            </div>
            <div className="flex items-center justify-end gap-3">
              <Button variant="outline" onClick={() => fetchEngineConfig()}>Reset</Button>
              <Button onClick={handleSaveConfig} disabled={isSavingConfig} className="bg-indigo-600 hover:bg-indigo-700">
                {isSavingConfig ? 'Aplicando...' : 'Aplicar y reiniciar'}
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
      <Tabs defaultValue="outputs" className="space-y-4">
        <TabsList>
          <TabsTrigger value="outputs">Agent Outputs</TabsTrigger>
          <TabsTrigger value="reasoning">Reasoning Bank</TabsTrigger>
          <TabsTrigger value="agents">Agent Status</TabsTrigger>
        </TabsList>

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
    </div>
  );
};