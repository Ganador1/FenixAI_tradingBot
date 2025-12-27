import { Router } from 'express';
import bcrypt from 'bcryptjs';
import jwt from 'jsonwebtoken';

const router = Router();

// Mock user data - In production, use database
const users = [
  {
    id: '1',
    email: 'admin@trading.com',
    password: '$2a$10$92IXUNpkjO0rOQ5byMi.Ye4oKoEa3Ro9llC/.og/at2.uheWG/igi', // password
    name: 'Admin User',
    role: 'administrator',
    is_active: true
  },
  {
    id: '2',
    email: 'trader@trading.com',
    password: '$2a$10$92IXUNpkjO0rOQ5byMi.Ye4oKoEa3Ro9llC/.og/at2.uheWG/igi', // password
    name: 'Trader User',
    role: 'trader',
    is_active: true
  },
  {
    id: '3',
    email: 'analyst@trading.com',
    password: '$2a$10$92IXUNpkjO0rOQ5byMi.Ye4oKoEa3Ro9llC/.og/at2.uheWG/igi', // password
    name: 'Analyst User',
    role: 'analyst',
    is_active: true
  }
];

// Login
router.post('/login', async (req, res) => {
  try {
    const { email, password, mfaCode } = req.body;

    // Validate input
    if (!email || !password) {
      return res.status(400).json({
        success: false,
        error: 'Email and password are required'
      });
    }

    // Find user
    const user = users.find(u => u.email === email);
    if (!user) {
      return res.status(401).json({
        success: false,
        error: 'Invalid credentials'
      });
    }

    // Check if user is active
    if (!user.is_active) {
      return res.status(401).json({
        success: false,
        error: 'Account is deactivated'
      });
    }

    // Verify password
    const isValidPassword = await bcrypt.compare(password, user.password);
    if (!isValidPassword) {
      return res.status(401).json({
        success: false,
        error: 'Invalid credentials'
      });
    }

    // Generate tokens
    const accessToken = jwt.sign(
      { 
        userId: user.id, 
        email: user.email, 
        role: user.role 
      },
      process.env.JWT_SECRET || 'your-secret-key',
      { expiresIn: '1h' }
    );

    const refreshToken = jwt.sign(
      { userId: user.id },
      process.env.JWT_REFRESH_SECRET || 'your-refresh-secret',
      { expiresIn: '7d' }
    );

    // Return user data (excluding password)
    const userData = {
      id: user.id,
      email: user.email,
      name: user.name,
      role: user.role,
      is_active: user.is_active
    };

    res.json({
      success: true,
      data: {
        accessToken,
        refreshToken,
        user: userData,
        permissions: getRolePermissions(user.role)
      }
    });
  } catch (error) {
    res.status(500).json({
      success: false,
      error: 'Login failed',
      details: (error as Error).message
    });
  }
});

// Refresh token
router.post('/refresh', (req, res) => {
  try {
    const { refreshToken } = req.body;

    if (!refreshToken) {
      return res.status(400).json({
        success: false,
        error: 'Refresh token is required'
      });
    }

    // Verify refresh token
    const decoded = jwt.verify(refreshToken, process.env.JWT_REFRESH_SECRET || 'your-refresh-secret') as any;
    
    // Find user
    const user = users.find(u => u.id === decoded.userId);
    if (!user) {
      return res.status(401).json({
        success: false,
        error: 'Invalid refresh token'
      });
    }

    // Generate new access token
    const newAccessToken = jwt.sign(
      { 
        userId: user.id, 
        email: user.email, 
        role: user.role 
      },
      process.env.JWT_SECRET || 'your-secret-key',
      { expiresIn: '1h' }
    );

    res.json({
      success: true,
      data: {
        accessToken: newAccessToken
      }
    });
  } catch (error) {
    res.status(401).json({
      success: false,
      error: 'Invalid refresh token'
    });
  }
});

// Logout
router.post('/logout', (req, res) => {
  // In a real implementation, you might want to blacklist the token
  res.json({
    success: true,
    message: 'Logged out successfully'
  });
});

// Get current user
router.get('/me', authenticateToken, (req, res) => {
  try {
    const userId = (req as any).user.userId;
    const user = users.find(u => u.id === userId);

    if (!user) {
      return res.status(404).json({
        success: false,
        error: 'User not found'
      });
    }

    const userData = {
      id: user.id,
      email: user.email,
      name: user.name,
      role: user.role,
      is_active: user.is_active
    };

    res.json({
      success: true,
      data: {
        user: userData,
        permissions: getRolePermissions(user.role)
      }
    });
  } catch (error) {
    res.status(500).json({
      success: false,
      error: 'Failed to get user information',
      details: (error as Error).message
    });
  }
});

// Middleware to authenticate token
function authenticateToken(req: any, res: any, next: any) {
  const authHeader = req.headers['authorization'];
  const token = authHeader && authHeader.split(' ')[1];

  if (!token) {
    return res.status(401).json({
      success: false,
      error: 'Access token is required'
    });
  }

  jwt.verify(token, process.env.JWT_SECRET || 'your-secret-key', (err: any, user: any) => {
    if (err) {
      return res.status(403).json({
        success: false,
        error: 'Invalid or expired token'
      });
    }

    req.user = user;
    next();
  });
}

// Helper function to get role permissions
function getRolePermissions(role: string): string[] {
  const permissions = {
    administrator: [
      'read:users', 'write:users', 'delete:users',
      'read:system', 'write:system',
      'read:trading', 'write:trading',
      'read:agents', 'write:agents',
      'read:reports', 'write:reports'
    ],
    trader: [
      'read:users',
      'read:trading', 'write:trading',
      'read:agents',
      'read:reports'
    ],
    analyst: [
      'read:users',
      'read:trading',
      'read:agents',
      'read:reports', 'write:reports'
    ],
    viewer: [
      'read:users',
      'read:trading',
      'read:agents',
      'read:reports'
    ]
  };

  return permissions[role as keyof typeof permissions] || [];
}

export default router;