import React, { useState, useEffect } from 'react';
import { LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import { Brain, Download, TrendingUp, Activity, Target, AlertCircle } from 'lucide-react';
// import { useAuthStore } from '../stores/authStore';
import { useAgentStore, ReasoningEntry } from '../stores/agentStore';
import { useSystemStore } from '../stores/systemStore';
import { Card, CardHeader, CardTitle, CardContent } from '../components/ui/Card';
import { Button } from '../components/ui/Button';
import { Input } from '../components/ui/Input';
import { Select } from '../components/ui/Select';
import { Badge } from '../components/ui/Badge';
import { Alert, AlertTitle, AlertDescription } from '../components/ui/Alert';



interface AgentConsensus {
  agent_id: string;
  agent_name: string;
  consensus_score: number;
  agreement_count: number;
  total_agents: number;
  dominant_sentiment: 'bullish' | 'bearish' | 'neutral';
  confidence: number;
}

interface ReasoningAnalytics {
  total_entries: number;
  avg_confidence: number;
  avg_accuracy: number;
  success_rate: number;
  top_performing_agents: string[];
  most_common_outcomes: Record<string, number>;
  confidence_trend: Array<{ date: string; confidence: number }>;
  outcome_distribution: Array<{ outcome: string; count: number }>;
}

export const ReasoningBank: React.FC = () => {
  // const { user } = useAuthStore();
  const { reasoningLogs, fetchReasoningLogs } = useAgentStore();
  const { socket } = useSystemStore();
  
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedAgent, setSelectedAgent] = useState<string>('all');
  const [selectedOutcome, setSelectedOutcome] = useState<string>('all');
  const [selectedTimeframe, setSelectedTimeframe] = useState<string>('24h');
  const [selectedConfidence, setSelectedConfidence] = useState<string>('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filteredLogs, setFilteredLogs] = useState<ReasoningEntry[]>([]);
  const [analytics, setAnalytics] = useState<ReasoningAnalytics | null>(null);
  const [consensus, setConsensus] = useState<AgentConsensus[]>([]);

  useEffect(() => {
    fetchReasoningBankData();
    
    if (socket) {
      socket.on('reasoningUpdate', handleReasoningUpdate);
      socket.on('consensusUpdate', handleConsensusUpdate);
    }

    return () => {
      if (socket) {
        socket.off('reasoningUpdate', handleReasoningUpdate);
        socket.off('consensusUpdate', handleConsensusUpdate);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [socket, selectedTimeframe]);

  const fetchReasoningBankData = async () => {
    try {
      setLoading(true);
      setError(null);

      await fetchReasoningLogs({ timeframe: selectedTimeframe });

      // Fetch analytics
      const analyticsResponse = await fetch(`/api/reasoning/analytics?timeframe=${selectedTimeframe}`);
      if (analyticsResponse.ok) {
        const analyticsData = await analyticsResponse.json();
        setAnalytics(analyticsData);
      }

      // Fetch consensus data
      const consensusResponse = await fetch(`/api/reasoning/consensus?timeframe=${selectedTimeframe}`);
      if (consensusResponse.ok) {
        const consensusData = await consensusResponse.json();
        setConsensus(consensusData);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch reasoning bank data');
    } finally {
      setLoading(false);
    }
  };

  const handleReasoningUpdate = (entry: ReasoningEntry) => {
    // This would be handled by the agent store
    console.log('Reasoning update:', entry);
  };

  const handleConsensusUpdate = (consensusData: AgentConsensus[]) => {
    setConsensus(consensusData);
  };

  const filterReasoningLogs = React.useCallback(() => {
    let filtered = reasoningLogs;

    if (searchQuery) {
      filtered = filtered.filter(log => 
        log.reasoning.toLowerCase().includes(searchQuery.toLowerCase()) ||
        log.decision.toLowerCase().includes(searchQuery.toLowerCase()) ||
        log.agent_name.toLowerCase().includes(searchQuery.toLowerCase())
      );
    }

    if (selectedAgent !== 'all') {
      filtered = filtered.filter(log => log.agent_id === selectedAgent);
    }

    if (selectedOutcome !== 'all') {
      filtered = filtered.filter(log => {
        if (!log.outcome) return false;
        // Check if the outcome object indicates success/failure based on accuracy
        if (selectedOutcome === 'success') return log.outcome.accuracy >= 0.7;
        if (selectedOutcome === 'failure') return log.outcome.accuracy < 0.3;
        if (selectedOutcome === 'partial') return log.outcome.accuracy >= 0.3 && log.outcome.accuracy < 0.7;
        return false;
      });
    }

    if (selectedConfidence !== 'all') {
      const confidenceRange = selectedConfidence.split('-');
      const min = parseFloat(confidenceRange[0]) / 100;
      const max = parseFloat(confidenceRange[1]) / 100;
      filtered = filtered.filter(log => 
        log.confidence >= min && log.confidence <= max
      );
    }

    setFilteredLogs(filtered);
  }, [reasoningLogs, searchQuery, selectedAgent, selectedOutcome, selectedConfidence]);

  useEffect(() => {
    filterReasoningLogs();
  }, [filterReasoningLogs, reasoningLogs, searchQuery, selectedAgent, selectedOutcome, selectedConfidence]);

  const getOutcomeColor = (outcome: ReasoningEntry['outcome']) => {
    if (!outcome) return 'default';
    if (outcome.accuracy > 70) return 'success';
    if (outcome.accuracy > 40) return 'warning';
    return 'error';
  };

  const getSentimentColor = (sentiment: string) => {
    switch (sentiment) {
      case 'bullish': return 'text-green-600';
      case 'bearish': return 'text-red-600';
      default: return 'text-gray-600';
    }
  };

  const getConfidenceColor = (confidence: number) => {
    if (confidence >= 0.8) return 'text-green-600';
    if (confidence >= 0.6) return 'text-yellow-600';
    return 'text-red-600';
  };

  const exportReasoningData = () => {
    const dataStr = JSON.stringify(filteredLogs, null, 2);
    const dataBlob = new Blob([dataStr], { type: 'application/json' });
    const url = URL.createObjectURL(dataBlob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `reasoning-bank-${selectedTimeframe}-${new Date().toISOString()}.json`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const renderReasoningEntry = (entry: ReasoningEntry) => {
    return (
      <div key={entry.id} className="border rounded-lg p-4 mb-4 hover:shadow-md transition-shadow">
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
            {entry.outcome && (
              <Badge variant={getOutcomeColor(entry.outcome)}>
                {entry.outcome.accuracy}%
              </Badge>
            )}
          </div>
        </div>

        {!!entry.input_data && typeof entry.input_data === 'object' && Object.keys(entry.input_data).length > 0 && (
          <div className="mb-3">
            <h5 className="text-sm font-medium text-gray-700 mb-1">Input Data:</h5>
            <div className="flex flex-wrap gap-2">
              {Object.entries(entry.input_data as Record<string, unknown>).slice(0, 5).map(([key, value]) => (
                <Badge key={key} variant="outline" className="text-xs">
                  {key}: {String(value)}
                </Badge>
              ))}
            </div>
          </div>
        )}

        {!!entry.input_data && typeof entry.input_data === 'object' && (
          <div className="mb-3">
            <h5 className="text-sm font-medium text-gray-700 mb-1">Input Data:</h5>
            <div className="bg-gray-50 rounded-lg p-3">
              <pre className="text-xs overflow-x-auto max-h-32">
                {JSON.stringify(entry.input_data, null, 2)}
              </pre>
            </div>
          </div>
        )}

        <div className="mb-3">
          <h5 className="text-sm font-medium text-gray-700 mb-1">Reasoning:</h5>
          <p className="text-sm bg-blue-50 rounded-lg p-3 leading-relaxed">{entry.reasoning}</p>
        </div>

        <div className="mb-3">
          <h5 className="text-sm font-medium text-gray-700 mb-1">Decision:</h5>
          <p className="text-sm bg-green-50 rounded-lg p-3 font-medium">{entry.decision}</p>
        </div>

        {entry.outcome && (
          <div className="mb-3">
            <h5 className="text-sm font-medium text-gray-700 mb-1">Outcome Analysis:</h5>
            <div className="bg-yellow-50 rounded-lg p-3">
              <div className="flex justify-between items-start mb-2">
                <span className="font-medium">Accuracy: {entry.outcome.accuracy}%</span>
                <Badge variant={entry.outcome.accuracy > 70 ? 'success' : 'error'}>
                  {entry.outcome.accuracy > 70 ? 'Success' : 'Needs Improvement'}
                </Badge>
              </div>
              <p className="text-sm">Actual: ${entry.outcome.actual_price}, Predicted: ${entry.outcome.predicted_price}</p>
              <p className="text-sm mt-1">{entry.outcome.judge_feedback}</p>
            </div>
          </div>
        )}
      </div>
    );
  };

  const renderConsensusCard = (consensus: AgentConsensus) => {
    return (
      <div key={consensus.agent_id} className="border rounded-lg p-4">
        <div className="flex justify-between items-start mb-2">
          <h4 className="font-semibold">{consensus.agent_name}</h4>
          <span className={`font-medium ${getSentimentColor(consensus.dominant_sentiment)}`}>
            {consensus.dominant_sentiment.toUpperCase()}
          </span>
        </div>
        <div className="space-y-2 text-sm">
          <div className="flex justify-between">
            <span className="text-gray-500">Consensus Score:</span>
            <span className="font-medium">{(consensus.consensus_score * 100).toFixed(1)}%</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">Agreement:</span>
            <span>{consensus.agreement_count}/{consensus.total_agents} agents</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">Confidence:</span>
            <span className={getConfidenceColor(consensus.confidence)}>
              {(consensus.confidence * 100).toFixed(1)}%
            </span>
          </div>
        </div>
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

      {/* Reasoning Bank Overview */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Total Entries</CardTitle>
            <Brain className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {analytics?.total_entries || 0}
            </div>
            <p className="text-xs text-muted-foreground">
              In selected timeframe
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
              {analytics ? (analytics.avg_confidence * 100).toFixed(1) : 0}%
            </div>
            <p className="text-xs text-muted-foreground">
              Across all entries
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Success Rate</CardTitle>
            <Target className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {analytics ? (analytics.success_rate * 100).toFixed(1) : 0}%
            </div>
            <p className="text-xs text-muted-foreground">
              Verified outcomes
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Active Agents</CardTitle>
            <Activity className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {new Set(reasoningLogs.map(log => log.agent_id)).size}
            </div>
            <p className="text-xs text-muted-foreground">
              Contributing agents
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Consensus Overview */}
      {consensus.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Agent Consensus Analysis</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {consensus.map(renderConsensusCard)}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Analytics Charts */}
      {analytics && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <Card>
            <CardHeader>
              <CardTitle>Confidence Trend</CardTitle>
            </CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={300}>
                <LineChart data={analytics.confidence_trend}>
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

          <Card>
            <CardHeader>
              <CardTitle>Outcome Distribution</CardTitle>
            </CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={300}>
                <BarChart data={analytics.outcome_distribution}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="outcome" />
                  <YAxis />
                  <Tooltip />
                  <Legend />
                  <Bar dataKey="count" fill="#3b82f6" name="Count" />
                </BarChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Search and Filters */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between">
            <span>Reasoning Bank Search</span>
            <Button
              variant="outline"
              size="sm"
              onClick={exportReasoningData}
              className="flex items-center space-x-1"
            >
              <Download className="h-4 w-4" />
              <span>Export</span>
            </Button>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Search</label>
              <Input
                placeholder="Search reasoning, conclusions, agents..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full"
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Agent</label>
              <Select
                value={selectedAgent}
                onChange={(e) => setSelectedAgent(e.target.value)}
              >
                <option value="all">All Agents</option>
                {Array.from(new Set(reasoningLogs.map(log => log.agent_id))).map(agentId => {
                  const agent = reasoningLogs.find(log => log.agent_id === agentId);
                  return (
                    <option key={agentId} value={agentId}>
                      {agent?.agent_name || agentId}
                    </option>
                  );
                })}
              </Select>
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Outcome</label>
              <Select
                value={selectedOutcome}
                onChange={(e) => setSelectedOutcome(e.target.value)}
              >
                <option value="all">All Outcomes</option>
                <option value="success">Success</option>
                <option value="failure">Failure</option>
                <option value="partial">Partial</option>
              </Select>
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Confidence</label>
              <Select
                value={selectedConfidence}
                onChange={(e) => setSelectedConfidence(e.target.value)}
              >
                <option value="all">All Levels</option>
                <option value="80-100">High (80-100%)</option>
                <option value="60-79">Medium (60-79%)</option>
                <option value="0-59">Low (0-59%)</option>
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
                <option value="90d">Last 90 Days</option>
              </Select>
            </div>
          </div>
          <div className="mt-4 flex items-center justify-between">
            <p className="text-sm text-gray-500">
              Showing {filteredLogs.length} of {reasoningLogs.length} entries
            </p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setSearchQuery('');
                setSelectedAgent('all');
                setSelectedOutcome('all');
                setSelectedConfidence('all');
              }}
            >
              Clear Filters
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Reasoning Entries */}
      <Card>
        <CardHeader>
          <CardTitle>Reasoning Bank Entries</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {filteredLogs.length > 0 ? (
              filteredLogs.slice(0, 20).map(renderReasoningEntry)
            ) : (
              <div className="text-center py-8 text-gray-500">
                No reasoning entries found matching your filters
              </div>
            )}
          </div>
          {filteredLogs.length > 20 && (
            <div className="mt-6 text-center">
              <Button variant="outline" size="sm">
                Load More Entries
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
};