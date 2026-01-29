/**
 * This is a user authentication API route demo.
 * Handle user registration, login, token management, etc.
 */
import { Router, type Request, type Response } from 'express'
import bcrypt from 'bcryptjs'
import jwt from 'jsonwebtoken'
import { randomUUID } from 'crypto'

const router = Router()

type DemoUser = {
  id: string
  email: string
  name: string
  role: 'admin' | 'trader'
  avatar: string
  created_at: string
}

type RegisteredUser = DemoUser & {
  passwordHash: string
}

const registeredUsers = new Map<string, RegisteredUser>()

const getJwtSecret = (): string | null => {
  const secret = process.env.JWT_SECRET || null
  return secret
}

const stripSensitive = (user: DemoUser | RegisteredUser): DemoUser => {
  if ('passwordHash' in user) {
    const { passwordHash: _, ...rest } = user
    return rest
  }
  return user
}

/**
 * User Login
 * POST /api/auth/register
 */
router.post('/register', async (req: Request, res: Response): Promise<void> => {
  try {
    const { email, password, name } = req.body as { email?: string; password?: string; name?: string }

    if (!email || !password) {
      res.status(400).json({
        success: false,
        error: 'Email and password are required'
      })
      return
    }

    const jwtSecret = getJwtSecret()
    if (!jwtSecret) {
      res.status(500).json({
        success: false,
        error: 'JWT_SECRET is not configured'
      })
      return
    }

    if (registeredUsers.has(email)) {
      res.status(409).json({
        success: false,
        error: 'User already exists'
      })
      return
    }

    const passwordHash = await bcrypt.hash(password, 10)
    const user: RegisteredUser = {
      id: randomUUID(),
      email,
      name: name || email.split('@')[0],
      role: 'trader',
      avatar: 'https://via.placeholder.com/150',
      created_at: new Date().toISOString(),
      passwordHash
    }

    registeredUsers.set(email, user)

    const token = jwt.sign(
      { sub: user.id, email: user.email, role: user.role },
      jwtSecret,
      { expiresIn: '1h' }
    )

    const userWithoutPassword = stripSensitive(user)

    res.status(201).json({
      success: true,
      user: userWithoutPassword,
      token
    })
  } catch (error) {
    console.error('Register error:', error)
    res.status(500).json({
      success: false,
      error: 'Internal server error'
    })
  }
})

/**
 * User Login
 * POST /api/auth/login
 */
router.post('/login', async (req: Request, res: Response): Promise<void> => {
  try {
    const { email, password } = req.body;

    // Validación básica
    if (!email || !password) {
      res.status(400).json({ 
        success: false, 
        error: 'Email and password are required' 
      });
      return;
    }

    // Credenciales demo según el frontend (solo si CREATE_DEMO_USERS=true o NODE_ENV=development)
    const createDemoUsers = process.env.CREATE_DEMO_USERS === 'true' || process.env.NODE_ENV === 'development';
    const defaultDemoPassword = process.env.DEFAULT_DEMO_PASSWORD || null;
    const demoUsers: DemoUser[] = createDemoUsers ? [
      {
        id: '1',
        email: 'admin@trading.com',
        name: 'Admin User',
        role: 'admin' as const,
        avatar: 'https://via.placeholder.com/150',
        created_at: new Date().toISOString()
      },
      {
        id: '2',
        email: 'trader@trading.com',
        name: 'Trader User',
        role: 'trader' as const,
        avatar: 'https://via.placeholder.com/150',
        created_at: new Date().toISOString()
      }
    ] : [];

    // Buscar usuario demo. Password validation:
    // - If DEFAULT_DEMO_PASSWORD is set, use it
    // - Else, if in development mode, allow 'password' for local testing
    const demoUser = demoUsers.find(u => u.email === email && (
      (defaultDemoPassword && password === defaultDemoPassword) ||
      (process.env.NODE_ENV === 'development' && password === 'password')
    ));

    const registeredUser = registeredUsers.get(email)

    const isRegisteredValid = registeredUser
      ? await bcrypt.compare(password, registeredUser.passwordHash)
      : false

    const user = demoUser || (isRegisteredValid ? registeredUser : null)

    if (!user) {
      res.status(401).json({ 
        success: false, 
        error: 'Invalid email or password' 
      });
      return;
    }

    const jwtSecret = getJwtSecret()
    if (!jwtSecret) {
      res.status(500).json({
        success: false,
        error: 'JWT_SECRET is not configured'
      })
      return;
    }

    // Generar token JWT real
    const token = jwt.sign(
      { sub: user.id, email: user.email, role: user.role },
      jwtSecret,
      { expiresIn: '1h' }
    )

    // Retornar usuario sin contraseña
    const userWithoutPassword = stripSensitive(user as DemoUser | RegisteredUser)

    res.status(200).json({
      success: true,
      user: userWithoutPassword,
      token
    });
  } catch (error) {
    console.error('Login error:', error);
    res.status(500).json({ 
      success: false, 
      error: 'Internal server error' 
    });
  }
})

/**
 * User Logout
 * POST /api/auth/logout
 */
router.post('/logout', async (req: Request, res: Response): Promise<void> => {
  res.status(200).json({
    success: true,
    message: 'Logged out'
  })
})

export default router
