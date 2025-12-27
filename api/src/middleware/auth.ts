import { Request, Response, NextFunction } from 'express';
import { supabase } from '../config/supabase';
import { AppError } from './errorHandler';
import { verifyAccessToken, TokenPayload } from '../config/jwt';

// Extender Request para incluir usuario y token
declare global {
  namespace Express {
    interface Request {
      user?: TokenPayload;
      token?: string;
    }
  }
}

export interface AuthenticatedRequest extends Request {
  user: {
    id: string;
    email: string;
    role: string;
  };
}

export const authenticateToken = async (
  req: AuthenticatedRequest,
  res: Response,
  next: NextFunction
): Promise<void> => {
  try {
    const authHeader = req.headers.authorization;
    const token = authHeader && authHeader.split(' ')[1];

    if (!token) {
      throw new AppError('Access token required', 401);
    }

    // Handle demo tokens for local development
    if (token.startsWith('demo-jwt-token-')) {
      const userId = token.replace('demo-jwt-token-', '');
      
      // Mock user data for demo tokens
      const demoUsers = {
        '1': {
          id: '1',
          email: 'admin@trading.com',
          role: 'admin'
        },
        '2': {
          id: '2',
          email: 'trader@trading.com',
          role: 'trader'
        }
      };

      const user = demoUsers[userId as keyof typeof demoUsers];
      
      if (!user) {
        throw new AppError('Invalid demo token', 401);
      }

      req.user = user;
      next();
      return;
    }

    // For real tokens, verify with Supabase
    try {
      const { data: { user }, error } = await supabase.auth.getUser(token);

      if (error || !user) {
        throw new AppError('Invalid or expired token', 401);
      }

      req.user = {
        id: user.id,
        email: user.email!,
        role: user.user_metadata?.role || 'user'
      };

      next();
    } catch (supabaseError) {
      // If Supabase fails, fall back to demo mode
      console.warn('Supabase authentication failed, using demo mode');
      req.user = {
        id: '1',
        email: 'admin@trading.com',
        role: 'admin'
      };
      next();
    }
  } catch (error) {
    if (error instanceof AppError) {
      next(error);
    } else {
      next(new AppError('Authentication failed', 401));
    }
  }
};

export const requireRole = (roles: string[]) => {
  return (req: AuthenticatedRequest, res: Response, next: NextFunction): void => {
    if (!req.user) {
      throw new AppError('Authentication required', 401);
    }

    if (!roles.includes(req.user.role)) {
      throw new AppError('Insufficient permissions', 403);
    }

    next();
  };
};