import { Router } from 'express';

const router = Router();

// Mock market data
const instruments = [
  {
    id: '1',
    symbol: 'BTCUSDT',
    name: 'Bitcoin/USDT',
    category: 'crypto',
    exchange: 'Binance',
    tick_size: 0.01,
    lot_size: 0.001,
    is_active: true
  },
  {
    id: '2',
    symbol: 'ETHUSDT',
    name: 'Ethereum/USDT',
    category: 'crypto',
    exchange: 'Binance',
    tick_size: 0.01,
    lot_size: 0.001,
    is_active: true
  },
  {
    id: '3',
    symbol: 'ADAUSDT',
    name: 'Cardano/USDT',
    category: 'crypto',
    exchange: 'Binance',
    tick_size: 0.0001,
    lot_size: 1,
    is_active: true
  },
  {
    id: '4',
    symbol: 'DOTUSDT',
    name: 'Polkadot/USDT',
    category: 'crypto',
    exchange: 'Binance',
    tick_size: 0.001,
    lot_size: 0.1,
    is_active: true
  },
  {
    id: '5',
    symbol: 'LINKUSDT',
    name: 'Chainlink/USDT',
    category: 'crypto',
    exchange: 'Binance',
    tick_size: 0.001,
    lot_size: 0.1,
    is_active: true
  }
];

// Get trading instruments
router.get('/instruments', (req, res) => {
  try {
    const { category, exchange, status } = req.query;
    
    let filteredInstruments = instruments;
    
    if (category) {
      filteredInstruments = filteredInstruments.filter(i => i.category === category);
    }
    
    if (exchange) {
      filteredInstruments = filteredInstruments.filter(i => i.exchange === exchange);
    }
    
    if (status) {
      filteredInstruments = filteredInstruments.filter(i => i.is_active === (status === 'active'));
    }
    
    res.json({
      success: true,
      data: {
        instruments: filteredInstruments,
        lastUpdate: new Date().toISOString()
      }
    });
  } catch (error) {
    res.status(500).json({
      success: false,
      error: 'Failed to get instruments',
      details: (error as Error).message
    });
  }
});

// Get price data for a specific symbol
router.get('/prices/:symbol', (req, res) => {
  try {
    const { symbol } = req.params;
    
    // Mock price data
    const basePrice = Math.random() * 1000 + 1000;
    const priceData = {
      symbol,
      bid: basePrice - Math.random() * 10,
      ask: basePrice + Math.random() * 10,
      last: basePrice,
      volume: Math.random() * 1000000,
      timestamp: new Date().toISOString()
    };
    
    res.json({
      success: true,
      data: priceData
    });
  } catch (error) {
    res.status(500).json({
      success: false,
      error: 'Failed to get price data',
      details: (error as Error).message
    });
  }
});

// Get historical price data
router.get('/historical/:symbol', (req, res) => {
  try {
    const { symbol } = req.params;
    const { timeframe = '1h', limit = 100 } = req.query;
    
    // Mock historical data
    const historicalData = [];
    const now = new Date();
    
    for (let i = 0; i < parseInt(limit as string); i++) {
      const time = new Date(now.getTime() - i * 60 * 60 * 1000); // 1 hour intervals
      const basePrice = Math.random() * 1000 + 1000;
      
      historicalData.push({
        timestamp: time.toISOString(),
        open: basePrice - Math.random() * 20,
        high: basePrice + Math.random() * 30,
        low: basePrice - Math.random() * 30,
        close: basePrice + (Math.random() - 0.5) * 20,
        volume: Math.random() * 1000000
      });
    }
    
    // Sort by timestamp (oldest first)
    historicalData.sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());
    
    res.json({
      success: true,
      data: {
        symbol,
        timeframe,
        data: historicalData
      }
    });
  } catch (error) {
    res.status(500).json({
      success: false,
      error: 'Failed to get historical data',
      details: (error as Error).message
    });
  }
});

// Get order book data
router.get('/orderbook/:symbol', (req, res) => {
  try {
    const { symbol } = req.params;
    const { depth = 20 } = req.query;
    
    // Mock order book data
    const basePrice = Math.random() * 1000 + 1000;
    const orderBookDepth = parseInt(depth as string);
    
    const bids = [];
    const asks = [];
    
    // Generate bid orders
    for (let i = 0; i < orderBookDepth; i++) {
      const price = basePrice - (i + 1) * 0.1;
      const quantity = Math.random() * 100 + 10;
      
      bids.push({
        price: parseFloat(price.toFixed(2)),
        quantity: parseFloat(quantity.toFixed(4)),
        total: parseFloat((price * quantity).toFixed(2))
      });
    }
    
    // Generate ask orders
    for (let i = 0; i < orderBookDepth; i++) {
      const price = basePrice + (i + 1) * 0.1;
      const quantity = Math.random() * 100 + 10;
      
      asks.push({
        price: parseFloat(price.toFixed(2)),
        quantity: parseFloat(quantity.toFixed(4)),
        total: parseFloat((price * quantity).toFixed(2))
      });
    }
    
    res.json({
      success: true,
      data: {
        symbol,
        bids,
        asks,
        timestamp: new Date().toISOString()
      }
    });
  } catch (error) {
    res.status(500).json({
      success: false,
      error: 'Failed to get order book data',
      details: (error as Error).message
    });
  }
});

// Get market sentiment
router.get('/sentiment/:symbol', (req, res) => {
  try {
    const { symbol } = req.params;
    
    // Mock sentiment data
    const sentimentData = {
      symbol,
      overall_sentiment: Math.random() > 0.5 ? 'bullish' : 'bearish',
      confidence_score: Math.random() * 0.4 + 0.6, // 0.6 to 1.0
      positive_count: Math.floor(Math.random() * 100) + 50,
      negative_count: Math.floor(Math.random() * 100) + 30,
      neutral_count: Math.floor(Math.random() * 50) + 20,
      keywords: [
        { word: 'bullish', count: Math.floor(Math.random() * 20) + 5 },
        { word: 'breakout', count: Math.floor(Math.random() * 15) + 3 },
        { word: 'support', count: Math.floor(Math.random() * 12) + 2 },
        { word: 'resistance', count: Math.floor(Math.random() * 10) + 2 }
      ],
      timestamp: new Date().toISOString()
    };
    
    res.json({
      success: true,
      data: sentimentData
    });
  } catch (error) {
    res.status(500).json({
      success: false,
      error: 'Failed to get market sentiment',
      details: (error as Error).message
    });
  }
});

export default router;