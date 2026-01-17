/**
 * API Configuration and Constants
 */

export const API_CONFIG = {
  // Base URL
  baseURL: '',

  // Request timeout in milliseconds
  timeout: 30000,

  // Retry configuration
  retries: 3,
  retryDelay: 1000,

  // Rate limiting
  maxRequestsPerSecond: 10,

  // Request headers
  defaultHeaders: {
    'Content-Type': 'application/json',
  },
};

export const API_ENDPOINTS = {
  // Auth
  auth: {
    login: '/api/auth/login',
    register: '/api/auth/register',
    logout: '/api/auth/logout',
    refresh: '/api/auth/refresh',
    me: '/api/auth/me',
  },

  // Market Data
  market: {
    symbols: '/api/market/symbols',
    quote: '/api/market/quote',
    history: '/api/market/history',
    overview: '/api/market/overview',
  },

  // Trading
  trading: {
    orders: '/api/trading/orders',
    createOrder: '/api/trading/orders',
    cancelOrder: (id: string) => `/api/trading/orders/${id}`,
    updateOrder: (id: string) => `/api/trading/orders/${id}`,
    positions: '/api/trading/positions',
    history: '/api/trading/history',
  },

  // Agents
  agents: {
    list: '/api/agents',
    get: (id: string) => `/api/agents/${id}`,
    create: '/api/agents',
    update: (id: string) => `/api/agents/${id}`,
    delete: (id: string) => `/api/agents/${id}`,
    run: (id: string) => `/api/agents/${id}/run`,
  },

  // System
  system: {
    health: '/api/health',
    metrics: '/api/system/metrics',
    status: '/api/system/status',
    logs: '/api/system/logs',
  },

  // Reasoning Bank
  reasoning: {
    list: '/api/reasoning',
    get: (id: string) => `/api/reasoning/${id}`,
    create: '/api/reasoning',
    update: (id: string) => `/api/reasoning/${id}`,
    delete: (id: string) => `/api/reasoning/${id}`,
  },
};

export const HTTP_STATUS = {
  OK: 200,
  CREATED: 201,
  BAD_REQUEST: 400,
  UNAUTHORIZED: 401,
  FORBIDDEN: 403,
  NOT_FOUND: 404,
  CONFLICT: 409,
  INTERNAL_ERROR: 500,
  SERVICE_UNAVAILABLE: 503,
};

export const ERROR_MESSAGES = {
  NETWORK_ERROR: 'Network error. Please check your connection.',
  TIMEOUT_ERROR: 'Request timeout. Please try again.',
  UNAUTHORIZED: 'Your session has expired. Please login again.',
  FORBIDDEN: 'You do not have permission to perform this action.',
  NOT_FOUND: 'Resource not found.',
  VALIDATION_ERROR: 'Please check your input and try again.',
  SERVER_ERROR: 'An error occurred on the server. Please try again later.',
  UNKNOWN_ERROR: 'An unknown error occurred. Please try again.',
};

export const SUCCESS_MESSAGES = {
  LOGIN_SUCCESS: 'Login successful!',
  LOGOUT_SUCCESS: 'Logout successful!',
  ORDER_CREATED: 'Order created successfully!',
  ORDER_CANCELLED: 'Order cancelled successfully!',
  SETTINGS_SAVED: 'Settings saved successfully!',
  OPERATION_SUCCESS: 'Operation completed successfully!',
};
