import React from 'react';
import { Card, CardHeader, CardContent, StatCard } from '@/components/ui/Card';
import { TrendingUp, TrendingDown, Activity, Zap, Shield, AlertCircle } from 'lucide-react';

/**
 * Dashboard Principal - Vista resumen
 */
export function DashboardOverview() {
  const stats = [
    {
      title: 'Portfolio Value',
      value: '$125,430',
      subtitle: 'Total Assets',
      icon: <Activity className="w-6 h-6" />,
      trend: { value: 12.5, isPositive: true },
    },
    {
      title: 'Daily Return',
      value: '+$2,450',
      subtitle: 'Today',
      icon: <TrendingUp className="w-6 h-6" />,
      trend: { value: 8.3, isPositive: true },
    },
    {
      title: 'Active Trades',
      value: '24',
      subtitle: 'Open positions',
      icon: <Zap className="w-6 h-6" />,
      trend: { value: 2.1, isPositive: false },
    },
    {
      title: 'System Status',
      value: 'Optimal',
      subtitle: 'All systems healthy',
      icon: <Shield className="w-6 h-6" />,
      trend: { value: 100, isPositive: true },
    },
  ];

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
      {stats.map((stat, index) => (
        <StatCard
          key={index}
          title={stat.title}
          value={stat.value}
          subtitle={stat.subtitle}
          icon={stat.icon}
          trend={stat.trend}
        />
      ))}
    </div>
  );
}

/**
 * Alertas y Notificaciones
 */
export function AlertsPanel() {
  const alerts = [
    {
      id: 1,
      title: 'High Volatility Detected',
      message: 'BTC/USD showing unusual price movement',
      severity: 'warning',
      timestamp: '5 min ago',
    },
    {
      id: 2,
      title: 'Trade Executed',
      message: 'BUY order for ETH completed at $2,450',
      severity: 'success',
      timestamp: '15 min ago',
    },
    {
      id: 3,
      title: 'Low Liquidity',
      message: 'USDC pool below optimal threshold',
      severity: 'danger',
      timestamp: '30 min ago',
    },
  ];

  const getSeverityColor = (severity: string) => {
    switch (severity) {
      case 'danger':
        return 'bg-red-500/20 border-red-500/30 text-red-400';
      case 'warning':
        return 'bg-amber-500/20 border-amber-500/30 text-amber-400';
      case 'success':
        return 'bg-emerald-500/20 border-emerald-500/30 text-emerald-400';
      default:
        return 'bg-cyan-500/20 border-cyan-500/30 text-cyan-400';
    }
  };

  return (
    <Card>
      <CardHeader title="Recent Alerts" />
      <CardContent>
        <div className="space-y-3">
          {alerts.map((alert) => (
            <div
              key={alert.id}
              className={`p-4 rounded-lg border flex items-start gap-4 ${getSeverityColor(
                alert.severity
              )}`}
            >
              <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5" />
              <div className="flex-1">
                <h4 className="font-semibold text-sm">{alert.title}</h4>
                <p className="text-sm opacity-75">{alert.message}</p>
                <span className="text-xs opacity-60 mt-1 block">{alert.timestamp}</span>
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

/**
 * Activity Feed
 */
export function ActivityFeed() {
  const activities = [
    {
      id: 1,
      action: 'Trade Executed',
      details: 'Bought 0.5 BTC @ $43,250',
      timestamp: '10:30 AM',
      icon: '📈',
    },
    {
      id: 2,
      action: 'Portfolio Updated',
      details: 'Rebalanced positions',
      timestamp: '10:15 AM',
      icon: '⚙️',
    },
    {
      id: 3,
      action: 'Alert Triggered',
      details: 'Stop loss activated for ETH',
      timestamp: '09:45 AM',
      icon: '🔔',
    },
  ];

  return (
    <Card>
      <CardHeader title="Activity" subtitle="Recent actions" />
      <CardContent>
        <div className="space-y-4">
          {activities.map((activity) => (
            <div key={activity.id} className="flex items-start gap-4 pb-4 border-b border-white/5 last:pb-0 last:border-b-0">
              <div className="text-2xl">{activity.icon}</div>
              <div className="flex-1">
                <h4 className="font-semibold text-sm text-slate-100">{activity.action}</h4>
                <p className="text-sm text-slate-400">{activity.details}</p>
                <span className="text-xs text-slate-500 mt-1 block">{activity.timestamp}</span>
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

/**
 * Quick Actions
 */
export function QuickActions() {
  const actions = [
    { label: 'New Trade', icon: '📊', color: 'bg-blue-500' },
    { label: 'View Charts', icon: '📈', color: 'bg-green-500' },
    { label: 'Settings', icon: '⚙️', color: 'bg-purple-500' },
    { label: 'Reports', icon: '📋', color: 'bg-orange-500' },
  ];

  return (
    <Card>
      <CardHeader title="Quick Actions" />
      <CardContent>
        <div className="grid grid-cols-2 gap-3">
          {actions.map((action, index) => (
            <button
              key={index}
              className="flex flex-col items-center justify-center p-4 rounded-lg bg-slate-800/50 hover:bg-slate-700/50 transition-all duration-200 border border-white/5 hover:border-cyan-500/30"
            >
              <span className="text-2xl mb-2">{action.icon}</span>
              <span className="text-sm font-medium text-slate-300">{action.label}</span>
            </button>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
