import React, { useState, useEffect } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import { RefreshCw } from 'lucide-react';
import { useSystemStore } from '../stores/systemStore';
import { Card, CardHeader, CardTitle, CardContent } from '../components/ui/Card';
import { Button } from '../components/ui/Button';
import { Badge } from '../components/ui/Badge';

// Utility functions
const formatPrice = (price: number) => {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  }).format(price);
};

/*
const formatMarketCap = (value: number) => {
  if (value >= 1e12) return `$${(value / 1e12).toFixed(2)}T`;
  if (value >= 1e9) return `$${(value / 1e9).toFixed(2)}B`;
  if (value >= 1e6) return `$${(value / 1e6).toFixed(2)}M`;
  return `$${value.toFixed(2)}`;
};
*/

// The market snapshot will be fetched from the backend via /api/trading/market

const INITIAL_HISTORY = [{ time: 'N/A', price: 0 }];

interface MarketSnapshot {
  symbol: string;
  price: number;
  change_24h: number;
  volume_24h: number;
  quote_volume_24h: number;
}

export const MarketData: React.FC = () => {
  const { connections } = useSystemStore();
  const [marketSnapshot, setMarketSnapshot] = useState<MarketSnapshot | null>(null);
  // const [selectedTimeframe, setSelectedTimeframe] = useState('1D');
  // const [selectedSymbol, setSelectedSymbol] = useState('all');
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [historical, setHistorical] = useState<{ time: string; price: number }[]>(INITIAL_HISTORY);
  const [sentiment, setSentiment] = useState<{ name: string; value: number; color: string }[]>([]);

  const handleRefresh = async () => {
    setIsRefreshing(true);
    await fetchLiveMarket();
    setIsRefreshing(false);
  };

  const fetchLiveMarket = async (symbol?: string) => {
    try {
      const resp = await fetch(`/api/trading/market${symbol ? `?symbol=${symbol}` : ''}`);
      if (!resp.ok) throw new Error('Failed to fetch market data');
      const data = await resp.json();
      setMarketSnapshot(data);
    } catch (err) {
      console.error('Market data fetch error', err);
      setMarketSnapshot(null);
    }
  };

  const fetchHistorical = async (symbol = 'BTCUSDT') => {
    try {
      const interval = '15m';
      const resp = await fetch(`/api/market/data/${symbol}?interval=${interval}&limit=24`);
      if (!resp.ok) throw new Error('Failed to fetch historical data');
      const data = await resp.json();
      const formatted = (data.data || []).map((p: Record<string, unknown>) => ({ 
        time: new Date(p.timestamp as string).toLocaleTimeString(), 
        price: p.price as number 
      }));
      setHistorical(formatted.length ? formatted : INITIAL_HISTORY);
    } catch (err) {
      console.error('Failed to fetch historical data', err);
      setHistorical(INITIAL_HISTORY);
    }
  };

  const fetchSentiment = async () => {
    try {
      const resp = await fetch('/api/reasoning/consensus?timeframe=24h');
      if (!resp.ok) throw new Error('Failed to fetch sentiment');
      const payload = await resp.json();
      const data = payload.data || payload.consensus || payload || [];
      // Convert to simple { name, value, color }
      const counts = { bullish: 0, bearish: 0, neutral: 0 };
      (data || []).forEach((d: Record<string, unknown>) => {
        const dom = (d.dominant_sentiment || d.dominant || d.dominantSentiment || d.dominantSentiment || d) as string;
        if (dom === 'bullish') counts.bullish += 1;
        else if (dom === 'bearish') counts.bearish += 1;
        else counts.neutral += 1;
      });

      setSentiment([
        { name: 'Bullish', value: counts.bullish, color: '#10b981' },
        { name: 'Bearish', value: counts.bearish, color: '#ef4444' },
        { name: 'Neutral', value: counts.neutral, color: '#6b7280' }
      ]);
    } catch (err) {
      console.error('Failed to fetch sentiment', err);
      setSentiment([]);
    }
  };

  useEffect(() => {
    fetchLiveMarket();
    fetchHistorical('BTCUSDT');
    fetchSentiment();
  }, []);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Market Data</h1>
          <p className="text-gray-600 mt-1">Real-time cryptocurrency market analysis</p>
        </div>
        <div className="flex items-center space-x-4">
          <div className="flex items-center space-x-2">
            <div className={`w-2 h-2 rounded-full ${connections.length > 0 ? 'bg-green-500' : 'bg-red-500'}`}></div>
            <span className="text-sm text-gray-600">
              {connections.length > 0 ? 'Connected' : 'Disconnected'}
            </span>
          </div>
          <Button
            onClick={handleRefresh}
            disabled={isRefreshing}
            variant="outline"
            size="sm"
          >
            <RefreshCw className={`w-4 h-4 mr-2 ${isRefreshing ? 'animate-spin' : ''}`} />
            Refresh Data
          </Button>
        </div>
      </div>

      {/* Market Overview */}
      <Card>
        <CardHeader>
          <CardTitle>Live Market Overview</CardTitle>
        </CardHeader>
        <CardContent>
          <MarketOverview marketSnapshot={marketSnapshot} />
        </CardContent>
      </Card>

      {/* Historical Analysis */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card>
          <CardHeader>
            <CardTitle>Historical Price Analysis</CardTitle>
            <p className="text-sm text-gray-500">7-day trend</p>
          </CardHeader>
          <CardContent>
            <div className="h-80">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={historical}>
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
                  <Line type="monotone" dataKey="price" stroke="#f59e0b" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
            <div className="flex justify-center space-x-6 mt-4 text-sm">
              <div className="flex items-center">
                <div className="w-3 h-3 bg-yellow-500 rounded mr-2"></div>
                <span className="text-gray-600">Bitcoin</span>
              </div>
              <div className="flex items-center">
                <div className="w-3 h-3 bg-purple-500 rounded mr-2"></div>
                <span className="text-gray-600">Ethereum</span>
              </div>
              <div className="flex items-center">
                <div className="w-3 h-3 bg-blue-500 rounded mr-2"></div>
                <span className="text-gray-600">Cardano</span>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Market Sentiment</CardTitle>
            <p className="text-sm text-gray-500">AI Agent Analysis</p>
          </CardHeader>
          <CardContent>
              <div className="space-y-4">
              <div className="flex items-center justify-between p-4 bg-green-50 rounded-lg border border-green-200">
                <div>
                  <h3 className="font-semibold text-green-900">Bullish Sentiment</h3>
                  <p className="text-sm text-green-700">Strong buy signals detected</p>
                </div>
                <div className="text-2xl font-bold text-green-600">{(sentiment[0]?.value || 0)}</div>
              </div>
              
              <div className="flex items-center justify-between p-4 bg-red-50 rounded-lg border border-red-200">
                <div>
                  <h3 className="font-semibold text-red-900">Bearish Sentiment</h3>
                  <p className="text-sm text-red-700">Sell pressure increasing</p>
                </div>
                <div className="text-2xl font-bold text-red-600">{(sentiment[1]?.value || 0)}</div>
              </div>

              <div className="flex items-center justify-between p-4 bg-gray-50 rounded-lg border border-gray-200">
                <div>
                  <h3 className="font-semibold text-gray-900">Neutral Sentiment</h3>
                  <p className="text-sm text-gray-700">Market consolidation phase</p>
                </div>
                <div className="text-2xl font-bold text-gray-600">{(sentiment[2]?.value || 0)}</div>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Market Indicators */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <Card>
          <CardHeader>
            <CardTitle>Volatility Index</CardTitle>
            <p className="text-sm text-gray-500">Market volatility measure</p>
          </CardHeader>
          <CardContent>
            <div className="text-center py-8">
              <div className="text-4xl font-bold text-yellow-600 mb-2">24.5</div>
              <div className="text-sm text-gray-600">Moderate Volatility</div>
              <div className="mt-4 bg-yellow-100 rounded-full h-2">
                <div className="bg-yellow-500 h-2 rounded-full w-3/5"></div>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Market Cap</CardTitle>
            <p className="text-sm text-gray-500">Total market capitalization</p>
          </CardHeader>
          <CardContent>
            <div className="text-center py-8">
              <div className="text-4xl font-bold text-blue-600 mb-2">$1.2T</div>
              <div className="text-sm text-gray-600">+5.2% (24h)</div>
              <div className="mt-4 text-xs text-gray-500">
                Bitcoin: $845B (70.4%)
                <br />
                Ethereum: $318B (26.5%)
                <br />
                Others: $37B (3.1%)
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Trading Volume</CardTitle>
            <p className="text-sm text-gray-500">24h trading volume</p>
          </CardHeader>
          <CardContent>
            <div className="text-center py-8">
              <div className="text-4xl font-bold text-green-600 mb-2">$89.2B</div>
              <div className="text-sm text-gray-600">+12.8% (24h)</div>
              <div className="mt-4 text-xs text-gray-500">
                Spot: $67.3B (75.4%)
                <br />
                Derivatives: $21.9B (24.6%)
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Market News */}
      <Card>
        <CardHeader>
          <CardTitle>Market News & Analysis</CardTitle>
          <p className="text-sm text-gray-500">Latest market insights</p>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <div className="p-4 bg-blue-50 rounded-lg border border-blue-200">
              <div className="flex items-start justify-between">
                <div className="flex-1">
                  <h4 className="font-semibold text-blue-900 mb-1">
                    Bitcoin ETF Approval Impact
                  </h4>
                  <p className="text-sm text-blue-700 mb-2">
                    Bitcoin price surges following positive ETF news and institutional adoption.
                  </p>
                  <div className="flex items-center space-x-4 text-xs text-blue-600">
                    <span>2 hours ago</span>
                    <span>Sentiment: Positive</span>
                    <span>Impact: High</span>
                  </div>
                </div>
                <div className="text-blue-600 font-semibold">+5.2%</div>
              </div>
            </div>

            <div className="p-4 bg-green-50 rounded-lg border border-green-200">
              <div className="flex items-start justify-between">
                <div className="flex-1">
                  <h4 className="font-semibold text-green-900 mb-1">
                    Ethereum Network Upgrade
                  </h4>
                  <p className="text-sm text-green-700 mb-2">
                    Ethereum 2.0 staking rewards increase as network activity grows.
                  </p>
                  <div className="flex items-center space-x-4 text-xs text-green-600">
                    <span>4 hours ago</span>
                    <span>Sentiment: Positive</span>
                    <span>Impact: Medium</span>
                  </div>
                </div>
                <div className="text-green-600 font-semibold">+3.1%</div>
              </div>
            </div>

            <div className="p-4 bg-yellow-50 rounded-lg border border-yellow-200">
              <div className="flex items-start justify-between">
                <div className="flex-1">
                  <h4 className="font-semibold text-yellow-900 mb-1">
                    Regulatory Developments
                  </h4>
                  <p className="text-sm text-yellow-700 mb-2">
                    New regulatory framework proposed for cryptocurrency trading platforms.
                  </p>
                  <div className="flex items-center space-x-4 text-xs text-yellow-700">
                    <span>6 hours ago</span>
                    <span>Sentiment: Neutral</span>
                    <span>Impact: Low</span>
                  </div>
                </div>
                <div className="text-yellow-600 font-semibold">-1.2%</div>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
};

const MarketOverview: React.FC<{marketSnapshot?: MarketSnapshot | null}> = ({marketSnapshot}) => {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4">
      {marketSnapshot ? (
        <div key={marketSnapshot.symbol} className="p-4 bg-gray-50 rounded-lg">
          <div className="flex items-center justify-between mb-2">
            <span className="font-semibold text-gray-900">{marketSnapshot.symbol}</span>
            <Badge variant={marketSnapshot.change_24h >= 0 ? 'success' : 'error'}>
              {marketSnapshot.change_24h >= 0 ? '+' : ''}{marketSnapshot.change_24h.toFixed(2)}%
            </Badge>
          </div>
          <div className="text-lg font-bold text-gray-900">
            {marketSnapshot.price ? formatPrice(marketSnapshot.price) : '-'}
          </div>
          <div className="text-sm text-gray-600">
            Vol: {marketSnapshot.volume_24h ? marketSnapshot.volume_24h.toLocaleString() : '-'}
          </div>
          <div className="text-sm text-gray-600">
            Cap: {marketSnapshot.quote_volume_24h ? marketSnapshot.quote_volume_24h.toLocaleString() : '-'}
          </div>
        </div>
      ) : (
        <div className="p-4 bg-gray-50 rounded-lg text-gray-600">Market data unavailable</div>
      )}
      
    </div>
  );
};