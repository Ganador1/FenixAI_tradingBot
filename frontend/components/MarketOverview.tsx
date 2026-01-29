import React, { useEffect, useState } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import { TrendingUp, TrendingDown } from 'lucide-react';

interface MarketOverviewItem {
  symbol: string;
  price: number;
  change_percent: number;
  price_change?: number;
  volume?: number;
  quote_volume?: number;
  high_24h?: number;
  low_24h?: number;
  name?: string;
}

interface ChartPoint {
  timestamp: string;
  price: number;
  volume?: number;
}

const symbolToName: Record<string, string> = {
  BTCUSDT: 'Bitcoin',
  ETHUSDT: 'Ethereum',
  SOLUSDT: 'Solana',
  BNBUSDT: 'BNB',
  ADAUSDT: 'Cardano',
};

export function MarketOverview() {
  const [selectedSymbol, setSelectedSymbol] = useState('BTCUSDT');
  const [marketData, setMarketData] = useState<MarketOverviewItem[]>([]);
  const [chartData, setChartData] = useState<ChartPoint[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [chartLoading, setChartLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchOverview = async () => {
      try {
        const response = await fetch('/api/market/overview');
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || data.error || 'Failed to load market overview');

        const markets: MarketOverviewItem[] = data.markets || data.data || [];
        setMarketData(markets);

        if (markets.length && !markets.find(m => m.symbol === selectedSymbol)) {
          setSelectedSymbol(markets[0].symbol);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load market overview');
      } finally {
        setIsLoading(false);
      }
    };

    fetchOverview();
    const interval = setInterval(fetchOverview, 15000);
    return () => clearInterval(interval);
  }, [selectedSymbol]);

  useEffect(() => {
    if (!selectedSymbol) return;
    const fetchSeries = async () => {
      try {
        setChartLoading(true);
        const response = await fetch(`/api/market/data/${selectedSymbol}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || data.error || 'Failed to load chart data');

        const series: ChartPoint[] = (data.data || data.points || []).map((p: { timestamp: string; price: number; volume?: number }) => ({
          timestamp: p.timestamp,
          price: p.price,
          volume: p.volume,
        }));
        setChartData(series);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load chart data');
      } finally {
        setChartLoading(false);
      }
    };

    fetchSeries();
  }, [selectedSymbol]);

  const selectedData = marketData.find(item => item.symbol === selectedSymbol);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-40 text-slate-400 text-sm">Cargando mercado en vivo...</div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-40 text-red-400 text-sm">{error}</div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Market Summary */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        {marketData.map((item) => {
          const changePct = item.change_percent ?? 0;
          return (
            <div
              key={item.symbol}
              className={`p-4 rounded-lg border cursor-pointer transition-all ${
                selectedSymbol === item.symbol 
                  ? 'border-blue-500 bg-blue-50' 
                  : 'border-gray-200 hover:border-gray-300'
              }`}
              onClick={() => setSelectedSymbol(item.symbol)}
            >
              <div className="text-sm font-medium text-gray-600">{item.symbol}</div>
              <div className="text-lg font-bold text-gray-900 mt-1">
                ${item.price.toLocaleString()}
              </div>
              <div className={`flex items-center mt-2 text-sm ${
                changePct >= 0 ? 'text-green-600' : 'text-red-600'
              }`}>
                {changePct >= 0 ? (
                  <TrendingUp className="w-4 h-4 mr-1" />
                ) : (
                  <TrendingDown className="w-4 h-4 mr-1" />
                )}
                {Math.abs(changePct).toFixed(2)}%
              </div>
            </div>
          );
        })}
      </div>

      {/* Price Chart */}
      {selectedData && (
        <div className="bg-gray-50 rounded-lg p-4">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-lg font-semibold text-gray-900">
                {selectedData.name || symbolToName[selectedData.symbol] || selectedData.symbol} ({selectedData.symbol})
              </h3>
              <div className="flex items-center space-x-4 mt-1">
                <span className="text-2xl font-bold text-gray-900">
                  ${selectedData.price.toLocaleString()}
                </span>
                <span className={`flex items-center text-sm ${
                  selectedData.change_percent >= 0 ? 'text-green-600' : 'text-red-600'
                }`}>
                  {selectedData.change_percent >= 0 ? (
                    <TrendingUp className="w-4 h-4 mr-1" />
                  ) : (
                    <TrendingDown className="w-4 h-4 mr-1" />
                  )}
                  {Math.abs(selectedData.change_percent).toFixed(2)}% 
                  ({selectedData.price_change !== undefined ? `$${Math.abs(selectedData.price_change).toLocaleString()}` : ''})
                </span>
              </div>
            </div>
            <div className="text-right text-sm text-gray-600">
              {selectedData.volume !== undefined && <div>Vol: {(selectedData.volume / 1e6).toFixed(1)}M</div>}
              {selectedData.quote_volume !== undefined && <div>Quote Vol: {(selectedData.quote_volume / 1e6).toFixed(1)}M</div>}
            </div>
          </div>

          <div className="h-64">
            {chartLoading ? (
              <div className="flex items-center justify-center h-full text-slate-400 text-sm">Cargando serie...</div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e0e0e0" />
                  <XAxis 
                    dataKey="timestamp" 
                    stroke="#666"
                    fontSize={12}
                    tickFormatter={(val) => new Date(val).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                  />
                  <YAxis 
                    stroke="#666"
                    fontSize={12}
                    domain={['dataMin', 'dataMax']}
                    tickFormatter={(val) => `$${val.toLocaleString()}`}
                  />
                  <Tooltip 
                    contentStyle={{
                      backgroundColor: 'white',
                      border: '1px solid #ccc',
                      borderRadius: '8px'
                    }}
                    formatter={(value: number) => [`$${value.toLocaleString()}`, 'Precio']}
                    labelFormatter={(val) => new Date(val).toLocaleString()}
                  />
                  <Line
                    type="monotone"
                    dataKey="price"
                    stroke="#3b82f6"
                    strokeWidth={2}
                    dot={false}
                    activeDot={{ r: 6, fill: '#3b82f6' }}
                  />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>
      )}
    </div>
  );
}