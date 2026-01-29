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
  status?: 'COMPLETED' | 'PENDING' | 'CANCELLED';
  executed_at?: string;
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
        return 'text-green-600 bg-green-50';
      case 'PENDING':
        return 'text-yellow-600 bg-yellow-50';
      case 'CANCELLED':
        return 'text-red-600 bg-red-50';
      default:
        return 'text-gray-600 bg-gray-50';
    }
  };

  return (
    <div className="space-y-3">
      {loading ? (
        <div className="text-sm text-gray-500">Loading trades...</div>
      ) : trades.length === 0 ? (
        <div className="text-sm text-gray-500">No recent trades</div>
      ) : (
        trades.map((trade) => (
          <div key={trade.id} className="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
          <div className="flex items-center space-x-3">
            <div className={`p-2 rounded-lg ${
              trade.side === 'BUY' ? 'bg-green-100' : 'bg-red-100'
            }`}>
              {trade.side === 'BUY' ? (
                <TrendingUp className="w-4 h-4 text-green-600" />
              ) : (
                <TrendingDown className="w-4 h-4 text-red-600" />
              )}
            </div>
            <div>
              <div className="flex items-center space-x-2">
                <span className="font-medium text-gray-900">{trade.symbol}</span>
                <span className={`text-xs px-2 py-1 rounded-full ${getStatusColor(trade.status ?? 'COMPLETED')}`}>
                  {trade.status ?? 'COMPLETED'}
                </span>
              </div>
              <div className="text-sm text-gray-600">
                {trade.side} {trade.quantity} @ ${trade.price.toLocaleString()}
              </div>
            </div>
          </div>
          <div className="text-right">
              <div className="font-medium text-gray-900">
                ${ (trade.quantity * trade.price).toLocaleString() }
            </div>
              <div className="text-sm text-gray-600">
                {formatTime(trade.executed_at || trade.timestamp)}
            </div>
          </div>
        </div>
        ))
      )}
      
      <div className="text-center pt-2">
        <button className="text-sm text-blue-600 hover:text-blue-800 font-medium">
          View all trades
        </button>
      </div>
    </div>
  );
}