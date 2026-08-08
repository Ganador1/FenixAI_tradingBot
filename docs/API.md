# 📡 FenixAI v2.0 - API Reference

## Overview

FenixAI provides a **REST API** (FastAPI) and **WebSocket** (Socket.IO) interface for real-time communication with the trading system.

**Base URL:** `http://localhost:8000`

---

## REST Endpoints

### Health Check

```http
GET /health
```

**Response:**

```json
{
  "status": "healthy",
  "version": "2.0.0",
  "timestamp": "2024-12-05T10:30:00Z",
  "components": {
    "trading_engine": "operational",
    "agents": "operational",
    "binance_connection": "connected"
  }
}
```

---

### Trading Status

```http
GET /api/v1/status
```

**Response:**

```json
{
  "mode": "paper",
  "symbol": "BTCUSDT",
  "timeframe": "15m",
  "is_running": true,
  "last_analysis": "2024-12-05T10:29:30Z",
  "active_position": null,
  "portfolio_balance": 10000.0
}
```

---

### Start Trading

```http
POST /api/v1/trading/start
```

**Request Body:**

```json
{
  "symbol": "BTCUSDT",
  "timeframe": "15m",
  "mode": "paper"
}
```

**Response:**

```json
{
  "status": "started",
  "message": "Trading engine started for BTCUSDT"
}
```

---

### Stop Trading

```http
POST /api/v1/trading/stop
```

**Response:**

```json
{
  "status": "stopped",
  "message": "Trading engine stopped"
}
```

---

### Get Current Analysis

```http
GET /api/v1/analysis/current
```

**Response:**

```json
{
  "timestamp": "2024-12-05T10:30:00Z",
  "symbol": "BTCUSDT",
  "current_price": 42150.50,
  "agents": {
    "technical": {
      "signal": "BUY",
      "confidence": 0.75,
      "reasoning": "RSI oversold with bullish divergence"
    },
    "visual": {
      "action": "BUY",
      "confidence": 0.72,
      "patterns": ["bullish_engulfing"]
    },
    "sentiment": {
      "sentiment": "POSITIVE",
      "confidence": 0.68
    },
    "qabba": {
      "signal": "BUY_QABBA",
      "confidence": 0.78,
      "squeeze": true
    }
  },
  "decision": {
    "action": "BUY",
    "confidence": 0.74,
    "approved_by_risk": true
  }
}
```

---

### Get Trade History

```http
GET /api/v1/trades
```

**Query Parameters:**

- `limit` (int): Number of trades to return (default: 50)
- `offset` (int): Pagination offset (default: 0)
- `status` (string): Filter by status (open, closed, all)

**Response:**

```json
{
  "trades": [
    {
      "id": "trade_123456",
      "symbol": "BTCUSDT",
      "side": "BUY",
      "entry_price": 42000.0,
      "exit_price": 42500.0,
      "quantity": 0.01,
      "pnl": 5.0,
      "pnl_percent": 1.19,
      "entry_time": "2024-12-05T09:00:00Z",
      "exit_time": "2024-12-05T10:00:00Z",
      "status": "closed"
    }
  ],
  "total": 150,
  "page": 1
}
```

---

### Get Agent Performance

```http
GET /api/v1/agents/performance
```

**Response:**

```json
{
  "agents": {
    "technical": {
      "total_signals": 150,
      "correct_signals": 98,
      "accuracy": 0.653,
      "avg_confidence": 0.72
    },
    "visual": {
      "total_signals": 150,
      "correct_signals": 92,
      "accuracy": 0.613,
      "avg_confidence": 0.68
    }
  },
  "overall_accuracy": 0.68
}
```

---

### Get Portfolio Status

```http
GET /api/v1/portfolio
```

**Response:**

```json
{
  "balance": 10150.50,
  "initial_balance": 10000.0,
  "total_pnl": 150.50,
  "total_pnl_percent": 1.505,
  "open_positions": [
    {
      "symbol": "BTCUSDT",
      "side": "LONG",
      "entry_price": 42000.0,
      "current_price": 42150.0,
      "quantity": 0.01,
      "unrealized_pnl": 1.50
    }
  ],
  "max_drawdown": -2.5,
  "win_rate": 0.62
}
```

---

### Update Configuration

```http
PUT /api/v1/config
```

**Request Body:**

```json
{
  "max_risk_per_trade": 0.02,
  "consensus_threshold": 0.70,
  "enable_visual_agent": true
}
```

**Response:**

```json
{
  "status": "updated",
  "config": { ... }
}
```

---

## WebSocket Events (Socket.IO)

### Connection

```javascript
import { io } from "socket.io-client";

const socket = io("http://localhost:8000", {
  transports: ["websocket"]
});

socket.on("connect", () => {
  console.log("Connected to FenixAI");
});
```

---

### Events (Server → Client)

#### `market_data`

Real-time market data updates.

```json
{
  "event": "market_data",
  "data": {
    "symbol": "BTCUSDT",
    "price": 42150.50,
    "volume_24h": 1234567890,
    "change_24h": 2.5,
    "timestamp": "2024-12-05T10:30:00Z"
  }
}
```

#### `analysis_update`

New analysis cycle completed.

```json
{
  "event": "analysis_update",
  "data": {
    "timestamp": "2024-12-05T10:30:00Z",
    "agents": { ... },
    "decision": { ... }
  }
}
```

#### `trade_executed`

Trade was executed.

```json
{
  "event": "trade_executed",
  "data": {
    "trade_id": "trade_123456",
    "symbol": "BTCUSDT",
    "side": "BUY",
    "price": 42000.0,
    "quantity": 0.01
  }
}
```

#### `position_update`

Position status changed.

```json
{
  "event": "position_update",
  "data": {
    "symbol": "BTCUSDT",
    "unrealized_pnl": 15.50,
    "current_price": 42155.0
  }
}
```

#### `alert`

System alert or warning.

```json
{
  "event": "alert",
  "data": {
    "level": "warning",
    "message": "High volatility detected",
    "timestamp": "2024-12-05T10:30:00Z"
  }
}
```

---

### Events (Client → Server)

#### `subscribe`

Subscribe to specific data streams.

```javascript
socket.emit("subscribe", {
  channels: ["market_data", "analysis_update", "trade_executed"]
});
```

#### `unsubscribe`

Unsubscribe from data streams.

```javascript
socket.emit("unsubscribe", {
  channels: ["market_data"]
});
```

---

## Authentication

Protected endpoints require the access token returned by `POST /api/auth/login`:

```http
Authorization: Bearer <your-jwt-token>
```

Access tokens are bound to server-side authentication state. A password change
invalidates all access tokens issued with the previous password.

User-management mutations are admin-only and require the administrator's current
password in the JSON `admin_password` field. Account creation and password-reset
requests return a short-lived token once; the account owner submits that token
with a new password to `POST /api/auth/password-reset/complete`. Reset tokens
must be sent in the JSON body, never in a URL.

---

## Error Responses

All errors follow this format:

```json
{
  "error": true,
  "code": "TRADING_ENGINE_ERROR",
  "message": "Failed to start trading engine",
  "details": "Ollama not available"
}
```

### Error Codes

| Code | Description |
|------|-------------|
| `VALIDATION_ERROR` | Invalid request parameters |
| `TRADING_ENGINE_ERROR` | Trading engine internal error |
| `BINANCE_ERROR` | Binance API error |
| `AGENT_ERROR` | Agent execution error |
| `UNAUTHORIZED` | Invalid or missing authentication |

---

## Rate Limits

| Endpoint | Limit |
|----------|-------|
| REST API | 100 requests/minute |
| WebSocket | No limit (connection-based) |

---

**See also:**

- [ARCHITECTURE.md](ARCHITECTURE.md) - System architecture
- [QUICKSTART.md](QUICKSTART.md) - Getting started guide
