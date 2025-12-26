/**
 * API Configuration
 */
export const API_CONFIG = {
  baseURL: import.meta.env.VITE_API_URL || 'http://localhost:3001/api',
  timeout: 30000,
  retries: 3,
  retryDelay: 1000,
  defaultHeaders: {
    'Content-Type': 'application/json',
  },
};

/**
 * HTTP Status codes
 */
export const HTTP_STATUS = {
  OK: 200,
  CREATED: 201,
  NO_CONTENT: 204,
  BAD_REQUEST: 400,
  UNAUTHORIZED: 401,
  FORBIDDEN: 403,
  NOT_FOUND: 404,
  CONFLICT: 409,
  UNPROCESSABLE_ENTITY: 422,
  TOO_MANY_REQUESTS: 429,
  INTERNAL_SERVER_ERROR: 500,
  BAD_GATEWAY: 502,
  SERVICE_UNAVAILABLE: 503,
} as const;

/**
 * Error messages
 */
export const ERROR_MESSAGES = {
  NETWORK_ERROR: 'Network error. Please check your connection.',
  TIMEOUT_ERROR: 'Request timeout. Please try again.',
  UNKNOWN_ERROR: 'An unexpected error occurred.',
  HTTP_400: 'Bad request. Please check your input.',
  HTTP_401: 'Unauthorized. Please login again.',
  HTTP_403: 'Forbidden. You do not have permission.',
  HTTP_404: 'Resource not found.',
  HTTP_409: 'Conflict. Resource already exists.',
  HTTP_422: 'Validation error. Please check your input.',
  HTTP_429: 'Too many requests. Please try again later.',
  HTTP_500: 'Server error. Please try again later.',
  HTTP_502: 'Bad gateway. Please try again later.',
  HTTP_503: 'Service unavailable. Please try again later.',
} as const;

/**
 * API Endpoints
 */
export const API_ENDPOINTS = {
  // Auth
  LOGIN: '/auth/login',
  LOGOUT: '/auth/logout',
  REGISTER: '/auth/register',
  REFRESH_TOKEN: '/auth/refresh',

  // Agents
  AGENTS: '/agents',
  AGENT_STATUS: '/agents/status',
  AGENT_CONFIG: '/agents/config',

  // Trading
  TRADES: '/trading/trades',
  POSITIONS: '/trading/positions',
  ORDERS: '/trading/orders',

  // Market
  MARKET_DATA: '/market/data',
  MARKET_SYMBOLS: '/market/symbols',

  // System
  SYSTEM_STATUS: '/system/status',
  SYSTEM_HEALTH: '/system/health',

  // Reasoning
  REASONING_BANK: '/reasoning/bank',
} as const;
