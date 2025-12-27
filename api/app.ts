/**
 * Express API Server Configuration
 * Trading Bot Backend API
 */

import express, {
  type Request,
  type Response,
  type NextFunction,
  Application,
} from 'express'
import cors from 'cors'
import path from 'path'
import dotenv from 'dotenv'
import { fileURLToPath } from 'url'
import helmet from 'helmet'
import rateLimit from 'express-rate-limit'

// Route imports
import authRoutes from './routes/auth.js'
import marketRoutes from './src/routes/market.js'
import tradingRoutes from './src/routes/trading.js'
import agentsRoutes from './src/routes/agents.js'
import systemRoutes from './src/routes/system.js'
import reasoningRoutes from './src/routes/reasoning.js'

// Middleware imports
import logger from './src/middleware/errorHandler.js'

// ESM compatibility
const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)

// Load environment variables
dotenv.config()

const app: Application = express()

// Security Middleware
app.use(helmet())

// CORS Configuration
const corsOptions = {
  origin: process.env.CORS_ORIGIN || 'http://localhost:5173',
  credentials: true,
  methods: ['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS'],
  allowedHeaders: ['Content-Type', 'Authorization'],
  maxAge: 86400, // 24 hours
}
app.use(cors(corsOptions))

// Rate Limiting
const limiter = rateLimit({
  windowMs: 15 * 60 * 1000, // 15 minutes
  max: 100, // limit each IP to 100 requests per windowMs
  message: 'Too many requests from this IP, please try again later.',
  standardHeaders: true,
  legacyHeaders: false,
})

const authLimiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  max: 5,
  skipSuccessfulRequests: true,
  message: 'Too many login attempts, please try again later.',
})

app.use('/api/', limiter)
app.use('/api/auth/login', authLimiter)
app.use('/api/auth/register', authLimiter)

// Body Parser Middleware
app.use(express.json({ limit: '10mb' }))
app.use(express.urlencoded({ extended: true, limit: '10mb' }))

// Request ID Middleware
app.use((req: Request, res: Response, next: NextFunction) => {
  req.id = `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`
  res.setHeader('X-Request-ID', req.id)
  next()
})

// Request Logging Middleware
app.use((req: Request, res: Response, next: NextFunction) => {
  const start = Date.now()
  res.on('finish', () => {
    const duration = Date.now() - start
    logger.info({
      method: req.method,
      path: req.path,
      status: res.statusCode,
      duration: `${duration}ms`,
      requestId: req.id,
    })
  })
  next()
})

/**
 * Health Check Endpoint
 */
app.get('/api/health', (req: Request, res: Response): void => {
  res.status(200).json({
    success: true,
    message: 'Server is running',
    timestamp: new Date().toISOString(),
    uptime: process.uptime(),
  })
})

/**
 * API Routes
 */
app.use('/api/auth', authRoutes)
app.use('/api/market', marketRoutes)
app.use('/api/trading', tradingRoutes)
app.use('/api/agents', agentsRoutes)
app.use('/api/system', systemRoutes)
app.use('/api/reasoning', reasoningRoutes)

/**
 * 404 Handler
 */
app.use((req: Request, res: Response): void => {
  logger.warn({
    message: '404 Not Found',
    path: req.path,
    method: req.method,
    requestId: req.id,
  })
  res.status(404).json({
    success: false,
    error: 'API endpoint not found',
    path: req.path,
    requestId: req.id,
  })
})

/**
 * Global Error Handler Middleware
 * Must be last middleware
 */
app.use((
  error: Error,
  req: Request,
  res: Response,
  next: NextFunction,
): void => {
  logger.error({
    message: error.message,
    stack: error.stack,
    path: req.path,
    method: req.method,
    requestId: req.id,
  })

  res.status(500).json({
    success: false,
    error: process.env.NODE_ENV === 'production'
      ? 'Internal server error'
      : error.message,
    requestId: req.id,
  })
})

export default app
