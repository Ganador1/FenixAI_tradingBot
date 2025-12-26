import React, { useEffect, useState } from 'react';
import { TrendingUp, TrendingDown } from 'lucide-react';

interface Trade {
  id: string;
  symbol: string;
  side: 'BUY' | 'SELL';
  quantity: number;
  price: number;
  total: number;
  timestamp: string;
  executed_at?: string;
  status: 'COMPLETED' | 'PENDING' | 'CANCELLED';
}

// RecentTrades component will fetch recent trades from the backend


export function RecentTrades() {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchTrades = async () => {
      try {
        setLoading(true);
        const resp = await fetch('/api/trading/history?limit=10');
        if (!resp.ok) throw new Error('Failed to fetch trades');
        const data = await resp.json();
        setTrades(data.trades || []);
      } catch (err) {
        console.error('Failed to fetch trades', err);
        setTrades([]);
      } finally {
        setLoading(false);
      }
    };

    fetchTrades();
  }, []);
  const formatTime = (timestamp: string) => {
    return new Date(timestamp).toLocaleTimeString('en-US', {
      hour: '2-digit',
      minute: '2-digit'
    });
  };

  const getStatusColor = (status: Trade['status']) => {
    switch (status) {
      case 'COMPLETED':
        return 'text-emerald-400 bg-emerald-500/20';
      case 'PENDING':
        return 'text-amber-400 bg-amber-500/20';
      case 'CANCELLED':
        return 'text-red-400 bg-red-500/20';
      default:
        return 'text-slate-400 bg-slate-500/20';
    }
  };

  return (
    <div className="space-y-3">
      {loading ? (
        <div className="text-sm text-slate-400">Loading trades...</div>
      ) : trades.length === 0 ? (
        <div className="text-sm text-slate-400">No recent trades</div>
      ) : (
        trades.map((trade) => (
          <div key={trade.id} className="flex items-center justify-between p-3 bg-slate-800/50 rounded-lg border border-white/5">
          <div className="flex items-center space-x-3">
            <div className={`p-2 rounded-lg ${
              trade.side === 'BUY' ? 'bg-emerald-500/20' : 'bg-red-500/20'
            }`}>
              {trade.side === 'BUY' ? (
                <TrendingUp className="w-4 h-4 text-emerald-400" />
              ) : (
                <TrendingDown className="w-4 h-4 text-red-400" />
              )}
            </div>
            <div>
              <div className="flex items-center space-x-2">
                <span className="font-medium text-slate-100">{trade.symbol}</span>
                <span className={`text-xs px-2 py-1 rounded-full ${getStatusColor((trade as any).status ?? 'COMPLETED')}`}>
                  {(trade as any).status ?? 'COMPLETED'}
                </span>
              </div>
              <div className="text-sm text-slate-400">
                {trade.side} {trade.quantity} @ ${trade.price.toLocaleString()}
              </div>
            </div>
          </div>
          <div className="text-right">
              <div className="font-medium text-slate-100">
                ${ (trade.quantity * trade.price).toLocaleString() }
            </div>
              <div className="text-sm text-slate-400">
                {formatTime(trade.executed_at || trade.timestamp)}
            </div>
          </div>
        </div>
        ))
      )}

      <div className="text-center pt-2">
        <button className="text-sm text-cyan-400 hover:text-cyan-300 font-medium">
          View all trades
        </button>
      </div>
    </div>
  );
}