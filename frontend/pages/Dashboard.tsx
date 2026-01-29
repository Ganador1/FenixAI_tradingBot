import React, { useEffect, useState } from 'react';
import { animate } from 'animejs';
import {
  TrendingUp,
  TrendingDown,
  DollarSign,
  Activity,
  Brain,
  Clock,
  RefreshCw,
  Zap,
  Shield
} from 'lucide-react';
import { useSystemStore } from '@/stores/systemStore';
import { useAgentStore } from '@/stores/agentStore';
import { formatCurrency, formatPercentage } from '@/lib/utils';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/Card';
import { MetricCard } from '@/components/MetricCard';
import { SystemStatusChart } from '@/components/SystemStatusChart';
import { AgentPerformanceChart } from '@/components/AgentPerformanceChart';
import { MarketOverview } from '@/components/MarketOverview';
import { RecentTrades } from '@/components/RecentTrades';
import { RecentAlerts } from '@/components/RecentAlerts';

interface PortfolioData {
  totalValue: number;
  change24h: number;
  changePercent: number;
  positions: number;
}

interface MarketPrice {
  symbol: string;
  price: number;
  change_24h: number;
}

export function Dashboard() {
  const { metrics, alerts } = useSystemStore();
  const { agents, scorecards, fetchAgents, fetchScorecards } = useAgentStore();

  // Real data states
  const [portfolio, setPortfolio] = useState<PortfolioData>({
    totalValue: 0,
    change24h: 0,
    changePercent: 0,
    positions: 0
  });
  const [marketPrice, setMarketPrice] = useState<MarketPrice | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [lastUpdate, setLastUpdate] = useState<Date>(new Date());

  // Fetch real data on mount
  useEffect(() => {
    fetchDashboardData();
    fetchAgents();
    fetchScorecards();

    // Anime.js entry animation
    animate('.animate-card', {
      translateY: [20, 0],
      opacity: [0, 1],
      delay: (_el: Element, i: number) => i * 100,
      easing: 'easeOutExpo',
      duration: 800
    });

    // Refresh every 30 seconds
    const interval = setInterval(fetchDashboardData, 30000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const fetchDashboardData = async () => {
    try {
      setIsLoading(true);

      // Fetch positions to calculate portfolio value
      const [positionsRes, marketRes] = await Promise.all([
        fetch('/api/trading/positions'),
        fetch('/api/trading/market')
      ]);

      if (positionsRes.ok) {
        const posData = await positionsRes.json();
        const positions = posData.positions || [];

        // Calculate portfolio metrics from positions
        const totalValue = positions.reduce((sum: number, pos: any) => {
          return sum + (pos.quantity * pos.current_price);
        }, 10000); // Base balance of 10000

        const unrealizedPnl = positions.reduce((sum: number, pos: any) => {
          return sum + (pos.unrealized_pnl || 0);
        }, 0);

        setPortfolio({
          totalValue,
          change24h: unrealizedPnl,
          changePercent: totalValue > 0 ? (unrealizedPnl / totalValue) * 100 : 0,
          positions: positions.length
        });
      }

      if (marketRes.ok) {
        const marketData = await marketRes.json();
        setMarketPrice(marketData);
      }

      setLastUpdate(new Date());
    } catch (error) {
      console.error('Failed to fetch dashboard data:', error);
    } finally {
      setIsLoading(false);
    }
  };

  // Calculate agent metrics
  const activeAgents = agents.filter(agent => agent.status === 'active').length;
  const totalAgents = agents.length || 6; // Default to 6 agents
  const avgAccuracy = scorecards.length > 0
    ? scorecards.reduce((sum, card) => sum + card.accuracy, 0) / scorecards.length
    : 0.78; // Default accuracy

  // Get recent alerts
  const recentAlerts = alerts.slice(0, 5);

  return (
    <div className="space-y-8 p-1">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-4xl font-bold text-white tracking-tight">
            Dashboard <span className="text-gradient">Pro</span>
          </h1>
          <p className="text-slate-400 mt-2">Real-time market intelligence & agent status.</p>
        </div>
        <div className="flex items-center space-x-3 bg-slate-800/50 p-2 rounded-xl border border-white/5 backdrop-blur-sm">
          <button
            onClick={fetchDashboardData}
            className="p-2 hover:bg-white/10 rounded-lg transition-all duration-200 text-cyan-400"
            disabled={isLoading}
            title="Refresh data"
          >
            <RefreshCw className={`w-5 h-5 ${isLoading ? 'animate-spin' : ''}`} />
          </button>
          <div className="flex items-center space-x-2 text-sm text-slate-400 px-2 border-l border-white/10">
            <Clock className="w-4 h-4" />
            <span>Updated: {lastUpdate.toLocaleTimeString()}</span>
          </div>
        </div>
      </div>

      {/* Market Price Banner */}
      {marketPrice && (
        <div className="glass-panel rounded-2xl p-6 relative overflow-hidden group">
          <div className="absolute top-0 right-0 w-64 h-64 bg-cyan-500/10 rounded-full blur-3xl -mr-16 -mt-16 transition-all duration-700 group-hover:bg-cyan-500/20"></div>

          <div className="flex items-center justify-between relative z-10">
            <div className="flex items-center space-x-4">
              <div className="p-3 bg-orange-500/20 rounded-xl">
                {/* Icon placeholder for coin logo */}
                <div className="w-8 h-8 rounded-full bg-orange-500 flex items-center justify-center text-white font-bold">₿</div>
              </div>
              <div>
                <p className="text-slate-400 text-sm font-medium tracking-wider">{marketPrice.symbol}</p>
                <div className="text-4xl font-bold text-white tracking-tight mt-1">
                  {formatCurrency(marketPrice.price)}
                </div>
              </div>
            </div>

            <div className={`flex items-center px-4 py-2 rounded-xl backdrop-blur-md border ${marketPrice.change_24h >= 0 ? 'bg-green-500/10 border-green-500/20 text-green-400' : 'bg-red-500/10 border-red-500/20 text-red-400'}`}>
              {marketPrice.change_24h >= 0 ? <TrendingUp className="w-6 h-6 mr-2" /> : <TrendingDown className="w-6 h-6 mr-2" />}
              <span className="text-2xl font-bold">{Math.abs(marketPrice.change_24h).toFixed(2)}%</span>
            </div>
          </div>
        </div>
      )}

      {/* Key Metrics */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <MetricCard
          title="Portfolio Value"
          value={formatCurrency(portfolio.totalValue)}
          change={portfolio.changePercent}
          changeType={portfolio.changePercent >= 0 ? 'positive' : 'negative'}
          icon={DollarSign}
          className="glass-card animate-card"
        />

        <MetricCard
          title="Active Agents"
          value={`${activeAgents}/${totalAgents}`}
          subtitle="AI Agents Running"
          change={activeAgents}
          changeType="positive"
          icon={Brain}
          className="glass-card animate-card"
        />

        <MetricCard
          title="System Health"
          value={metrics?.cpu ? `${metrics.cpu.toFixed(1)}%` : 'N/A'}
          subtitle="CPU Usage"
          change={metrics?.cpu ? metrics.cpu - 50 : 0}
          changeType={metrics?.cpu && metrics.cpu < 80 ? 'positive' : 'negative'}
          icon={Activity}
          className="glass-card animate-card"
        />

        <MetricCard
          title="Agent Accuracy"
          value={formatPercentage(avgAccuracy)}
          subtitle="Avg Win Rate"
          change={avgAccuracy - 75}
          changeType={avgAccuracy >= 75 ? 'positive' : 'negative'}
          icon={Zap}
          className="glass-card animate-card"
        />
      </div>

      {/* Main Content Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Left Column */}
        <div className="lg:col-span-2 space-y-8">
          {/* Market Overview */}
          <div className="glass-panel rounded-2xl p-6 animate-card">
            <h3 className="text-xl font-semibold mb-6 flex items-center">
              <Activity className="w-5 h-5 mr-2 text-cyan-400" />
              Market Overview
            </h3>
            <MarketOverview />
          </div>

          {/* System & Agents Chart Row */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="glass-panel rounded-2xl p-6 animate-card">
              <h3 className="text-lg font-semibold mb-4 text-slate-200">System Load</h3>
              <div className="h-[200px]">
                <SystemStatusChart />
              </div>
            </div>

            <div className="glass-panel rounded-2xl p-6 animate-card">
              <h3 className="text-lg font-semibold mb-4 text-slate-200">Agent Performance</h3>
              <div className="h-[200px]">
                <AgentPerformanceChart />
              </div>
            </div>
          </div>
        </div>

        {/* Right Column */}
        <div className="space-y-8">
          {/* Quick Actions */}
          <div className="glass-panel rounded-2xl p-6 animate-card">
            <h3 className="text-lg font-semibold mb-4 text-white">Quick Actions</h3>
            <div className="space-y-3">
              <button className="w-full text-left p-4 bg-gradient-to-r from-blue-600/20 to-blue-400/10 border border-blue-500/20 rounded-xl hover:bg-blue-600/30 transition-all group">
                <div className="font-semibold text-blue-300 group-hover:text-blue-200 flex items-center">
                  <DollarSign className="w-4 h-4 mr-2" /> New Order
                </div>
                <div className="text-xs text-blue-400/60 mt-1">Execute manual trade</div>
              </button>

              <button className="w-full text-left p-4 bg-gradient-to-r from-emerald-600/20 to-emerald-400/10 border border-emerald-500/20 rounded-xl hover:bg-emerald-600/30 transition-all group">
                <div className="font-semibold text-emerald-300 group-hover:text-emerald-200 flex items-center">
                  <Brain className="w-4 h-4 mr-2" /> View Agents
                </div>
                <div className="text-xs text-emerald-400/60 mt-1">Check AI reasoning</div>
              </button>

              <button className="w-full text-left p-4 bg-gradient-to-r from-purple-600/20 to-purple-400/10 border border-purple-500/20 rounded-xl hover:bg-purple-600/30 transition-all group">
                <div className="font-semibold text-purple-300 group-hover:text-purple-200 flex items-center">
                  <Shield className="w-4 h-4 mr-2" /> System Health
                </div>
                <div className="text-xs text-purple-400/60 mt-1">Monitor infrastructure</div>
              </button>
            </div>
          </div>

          {/* Recent Alerts */}
          <div className="glass-panel rounded-2xl p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold text-white">Recent Alerts</h3>
              <span className="text-xs px-2 py-1 rounded-full bg-slate-700 text-slate-300">{recentAlerts.length} new</span>
            </div>
            <RecentAlerts alerts={recentAlerts} />
          </div>

          {/* Recent Trades */}
          <div className="glass-panel rounded-2xl p-6">
            <h3 className="text-lg font-semibold mb-4 text-white">Recent Trades</h3>
            <RecentTrades />
          </div>
        </div>
      </div>
    </div>
  );
}