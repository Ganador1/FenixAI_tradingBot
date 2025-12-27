/**
 * JWT Authentication Configuration
 * Implementar tokens JWT con refresh tokens
 */

import jwt from 'jsonwebtoken';

export interface TokenPayload {
  id: string;
  email: string;
  role: 'admin' | 'trader' | 'viewer';
  iat?: number;
  exp?: number;
}

export interface AuthTokens {
  accessToken: string;
  refreshToken: string;
  expiresIn: number;
}

export const JWT_CONFIG = {
  // Access token - short lived (15 minutes)
  accessTokenExpiry: '15m',
  accessTokenSecret: process.env.JWT_ACCESS_SECRET || 'your-secret-key-access',

  // Refresh token - long lived (7 days)
  refreshTokenExpiry: '7d',
  refreshTokenSecret: process.env.JWT_REFRESH_SECRET || 'your-secret-key-refresh',

  // Verification options
  verifyOptions: {
    algorithms: ['HS256'],
  },
};

/**
 * Generate access and refresh tokens
 */
export function generateTokens(payload: Omit<TokenPayload, 'iat' | 'exp'>): AuthTokens {
  const accessToken = jwt.sign(payload, JWT_CONFIG.accessTokenSecret, {
    expiresIn: JWT_CONFIG.accessTokenExpiry,
    algorithm: 'HS256',
  });

  const refreshToken = jwt.sign(payload, JWT_CONFIG.refreshTokenSecret, {
    expiresIn: JWT_CONFIG.refreshTokenExpiry,
    algorithm: 'HS256',
  });

  // Calculate expires in seconds (15 minutes)
  const expiresIn = 15 * 60;

  return {
    accessToken,
    refreshToken,
    expiresIn,
  };
}

/**
 * Verify access token
 */
export function verifyAccessToken(token: string): TokenPayload | null {
  try {
    const decoded = jwt.verify(token, JWT_CONFIG.accessTokenSecret, {
      algorithms: ['HS256'],
    }) as TokenPayload;
    return decoded;
  } catch (error) {
    console.error('Access token verification failed:', error);
    return null;
  }
}

/**
 * Verify refresh token
 */
export function verifyRefreshToken(token: string): TokenPayload | null {
  try {
    const decoded = jwt.verify(token, JWT_CONFIG.refreshTokenSecret, {
      algorithms: ['HS256'],
    }) as TokenPayload;
    return decoded;
  } catch (error) {
    console.error('Refresh token verification failed:', error);
    return null;
  }
}

/**
 * Decode token without verification (for debugging)
 */
export function decodeToken(token: string): TokenPayload | null {
  try {
    const decoded = jwt.decode(token) as TokenPayload;
    return decoded;
  } catch (error) {
    console.error('Token decode failed:', error);
    return null;
  }
}

/**
 * Check if token is expired
 */
export function isTokenExpired(token: string): boolean {
  try {
    const decoded = jwt.decode(token) as TokenPayload;
    if (!decoded || !decoded.exp) return true;

    const currentTime = Math.floor(Date.now() / 1000);
    return decoded.exp < currentTime;
  } catch (error) {
    return true;
  }
}

/**
 * Get time until token expiration (in seconds)
 */
export function getTokenExpirationTime(token: string): number {
  try {
    const decoded = jwt.decode(token) as TokenPayload;
    if (!decoded || !decoded.exp) return 0;

    const currentTime = Math.floor(Date.now() / 1000);
    return Math.max(0, decoded.exp - currentTime);
  } catch (error) {
    return 0;
  }
}

// Mock database of users - Replace with real database
export const mockUsers = {
  'admin@trading.com': {
    id: '1',
    email: 'admin@trading.com',
    password: 'hashed_password_123', // In production, use bcrypt
    role: 'admin' as const,
  },
  'trader@trading.com': {
    id: '2',
    email: 'trader@trading.com',
    password: 'hashed_password_456',
    role: 'trader' as const,
  },
  'viewer@trading.com': {
    id: '3',
    email: 'viewer@trading.com',
    password: 'hashed_password_789',
    role: 'viewer' as const,
  },
};

// Demo token for testing (expires in 24 hours)
export const DEMO_TOKEN_EXPIRY = '24h';
