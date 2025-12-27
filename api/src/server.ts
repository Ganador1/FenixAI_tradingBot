import express from 'express';
import cors from 'cors';
import helmet from 'helmet';
import rateLimit from 'express-rate-limit';
import { createServer } from 'http';
import { Server } from 'socket.io';
import winston from 'winston';
import { connectionManager } from './services/connectionManager';
import { systemMonitor } from './services/systemMonitor';
import { agentService } from './services/agentService';

// Import routes
import authRoutes from './routes/auth';
import marketRoutes from './routes/market';
import tradingRoutes from './routes/trading';
import systemRoutes from './routes/system';
import agentRoutes from './routes/agents';
import reasoningRoutes from './routes/reasoning';

const app = express();
const server = createServer(app);
const io = new Server(server, {
  cors: {
    origin: process.env.FRONTEND_URL || "http://localhost:5173",
    methods: ["GET", "POST"]
  }
});

// Logger setup
const logger = winston.createLogger({
  level: 'info',
  format: winston.format.combine(
    winston.format.timestamp(),
    winston.format.json()
  ),
  transports: [
    new winston.transports.Console(),
    new winston.transports.File({ filename: 'logs/server.log' }),
    new winston.transports.File({ filename: 'logs/error.log', level: 'error' })
  ]
});

// Middleware
app.use(helmet({
  contentSecurityPolicy: {
    directives: {
      defaultSrc: ["'self'"],
      styleSrc: ["'self'", "'unsafe-inline'", "https://fonts.googleapis.com"],
      fontSrc: ["'self'", "https://fonts.gstatic.com"],
      scriptSrc: ["'self'", "'unsafe-inline'"],
      imgSrc: ["'self'", "data:", "https:"],
      connectSrc: ["'self'", "ws:", "wss:"]
    }
  }
}));

app.use(cors({
  origin: process.env.FRONTEND_URL || "http://localhost:5173",
  credentials: true
}));

app.use(express.json({ limit: '10mb' }));
app.use(express.urlencoded({ extended: true }));

// Rate limiting
const limiter = rateLimit({
  windowMs: 15 * 60 * 1000, // 15 minutes
  max: 1000, // limit each IP to 1000 requests per windowMs
  message: {
    error: 'Too many requests from this IP, please try again later.'
  },
  standardHeaders: true,
  legacyHeaders: false,
});

app.use('/api/', limiter);

// Request logging
app.use((req, res, next) => {
  logger.info(`${req.method} ${req.path}`, {
    ip: req.ip,
    userAgent: req.get('User-Agent'),
    timestamp: new Date().toISOString()
  });
  next();
});

// Routes
app.use('/api/auth', authRoutes);
app.use('/api/market', marketRoutes);
app.use('/api/trading', tradingRoutes);
app.use('/api/system', systemRoutes);
app.use('/api/agents', agentRoutes);
app.use('/api/reasoning-bank', reasoningRoutes);

// Health check endpoint
app.get('/health', (req, res) => {
  const systemStatus = systemMonitor.getSystemStatus();
  res.json({
    status: 'ok',
    timestamp: new Date().toISOString(),
    uptime: process.uptime(),
    system: systemStatus
  });
});

// WebSocket connection handling
io.on('connection', (socket) => {
  logger.info(`Client connected: ${socket.id}`);

  socket.on('subscribe:market', (symbols: string[]) => {
    symbols.forEach(symbol => {
      socket.join(`market:${symbol}`);
    });
    logger.info(`Client ${socket.id} subscribed to market data: ${symbols.join(', ')}`);
  });

  socket.on('subscribe:agents', () => {
    socket.join('agents');
    logger.info(`Client ${socket.id} subscribed to agent updates`);
  });

  socket.on('subscribe:system', () => {
    socket.join('system');
    logger.info(`Client ${socket.id} subscribed to system updates`);
  });

  socket.on('disconnect', () => {
    logger.info(`Client disconnected: ${socket.id}`);
  });
});

// Initialize services and start server
async function initializeServices() {
  try {
    logger.info('Initializing services...');

    // Initialize connection manager
    await connectionManager.initializeConnection({
      id: 'supabase-main',
      type: 'supabase',
      url: process.env.SUPABASE_URL || 'https://your-project.supabase.co',
      apiKey: process.env.SUPABASE_ANON_KEY || 'your-anon-key'
    });

    await connectionManager.initializeConnection({
      id: 'redis-cache',
      type: 'redis',
      url: process.env.REDIS_URL || 'redis://localhost:6379'
    });

    // Initialize system monitor
    systemMonitor.startMonitoring(30000); // 30 second intervals

    // Initialize agent service
    await agentService.initialize();

    logger.info('Services initialized successfully');
  } catch (error) {
    logger.error('Failed to initialize services:', error);
    throw error;
  }
}

// Set up real-time data streaming
function setupRealTimeUpdates() {
  // Market data updates (simulated for demo)
  setInterval(() => {
    const symbols = ['BTCUSDT', 'ETHUSDT', 'ADAUSDT', 'DOTUSDT', 'LINKUSDT'];
    const updates = symbols.map(symbol => ({
      symbol,
      bid: Math.random() * 1000 + 1000,
      ask: Math.random() * 1000 + 1001,
      last: Math.random() * 1000 + 1000.5,
      volume: Math.random() * 1000000,
      timestamp: new Date().toISOString()
    }));

    updates.forEach(update => {
      io.to(`market:${update.symbol}`).emit('price:update', update);
    });
  }, 1000); // Update every second

  // Agent updates
  agentService.on('agent:reasoning', (data) => {
    io.to('agents').emit('agent:reasoning', data);
  });

  agentService.on('scorecard:updated', (data) => {
    io.to('agents').emit('agent:scorecard', data);
  });

  // System updates
  systemMonitor.on('metrics:updated', (metrics) => {
    io.to('system').emit('system:metrics', metrics);
  });

  systemMonitor.on('alert:created', (alert) => {
    io.to('system').emit('system:alert', alert);
  });

  connectionManager.on('connection:status', (status) => {
    io.to('system').emit('system:connection', status);
  });
}

// Graceful shutdown
process.on('SIGTERM', async () => {
  logger.info('SIGTERM received, shutting down gracefully...');
  await shutdown();
});

process.on('SIGINT', async () => {
  logger.info('SIGINT received, shutting down gracefully...');
  await shutdown();
});

async function shutdown() {
  try {
    logger.info('Shutting down services...');
    
    systemMonitor.stopMonitoring();
    await agentService.shutdown();
    await connectionManager.shutdown();
    
    server.close(() => {
      logger.info('Server closed');
      process.exit(0);
    });
  } catch (error) {
    logger.error('Error during shutdown:', error);
    process.exit(1);
  }
}

// Start server
const PORT = process.env.PORT || 3001;

async function startServer() {
  try {
    await initializeServices();
    setupRealTimeUpdates();
    
    server.listen(PORT, () => {
      logger.info(`Server running on port ${PORT}`);
      logger.info(`Environment: ${process.env.NODE_ENV || 'development'}`);
    });
  } catch (error) {
    logger.error('Failed to start server:', error);
    process.exit(1);
  }
}

startServer();

export default app;