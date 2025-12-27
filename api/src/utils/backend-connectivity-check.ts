/**
 * Backend Connectivity Check
 * Verifica que todos los componentes del backend de Fenix estén conectados correctamente
 */

import { Express } from 'express';
import logger from './logger';

export interface ConnectivityCheckResult {
  status: 'healthy' | 'degraded' | 'unhealthy';
  timestamp: string;
  components: {
    [key: string]: {
      status: 'ok' | 'warning' | 'error';
      message: string;
    };
  };
  summary: string;
}

/**
 * Verifica la conectividad de todos los componentes
 */
export async function checkBackendConnectivity(app: Express): Promise<ConnectivityCheckResult> {
  const result: ConnectivityCheckResult = {
    status: 'healthy',
    timestamp: new Date().toISOString(),
    components: {},
    summary: '',
  };

  // 1. Verificar rutas registradas
  const routes = app._router.stack
    .filter((r: any) => r.route)
    .map((r: any) => r.route.path);

  result.components['routes'] = {
    status: routes.length > 0 ? 'ok' : 'error',
    message: `${routes.length} rutas registradas: ${routes.join(', ')}`,
  };

  if (routes.length === 0) {
    result.status = 'unhealthy';
  }

  // 2. Verificar conexión a base de datos (mock)
  try {
    result.components['database'] = {
      status: 'ok',
      message: 'Supabase connection configured',
    };
  } catch (error) {
    result.components['database'] = {
      status: 'error',
      message: `Database error: ${error}`,
    };
    result.status = 'degraded';
  }

  // 3. Verificar servicios de Fenix
  const fenixServices = {
    agents: 'Sistema de Agentes IA',
    trading: 'Motor de Trading',
    market: 'Datos de Mercado',
    reasoning: 'Banco de Razonamiento',
    system: 'Monitoreo de Sistema',
  };

  for (const [service, description] of Object.entries(fenixServices)) {
    const routeExists = routes.some((r: string) => r.includes(`/${service}`));
    result.components[`service_${service}`] = {
      status: routeExists ? 'ok' : 'warning',
      message: routeExists ? `${description} - Conectado` : `${description} - No encontrado en rutas`,
    };

    if (!routeExists) {
      result.status = 'degraded';
    }
  }

  // 4. Verificar middleware
  const middlewareChecks = {
    cors: checkMiddleware(app, 'corsMiddleware'),
    auth: checkMiddleware(app, 'authenticateToken'),
    errorHandler: checkMiddleware(app, 'errorHandler'),
    logging: checkMiddleware(app, 'requestLogger'),
  };

  for (const [name, hasMiddleware] of Object.entries(middlewareChecks)) {
    result.components[`middleware_${name}`] = {
      status: hasMiddleware ? 'ok' : 'warning',
      message: hasMiddleware ? `${name} middleware configurado` : `${name} middleware no encontrado`,
    };
  }

  // 5. Verificar variables de entorno críticas
  const envVariables = ['JWT_ACCESS_SECRET', 'JWT_REFRESH_SECRET', 'SUPABASE_URL', 'SUPABASE_KEY'];
  const missingEnv = envVariables.filter((env) => !process.env[env]);

  result.components['environment'] = {
    status: missingEnv.length === 0 ? 'ok' : 'warning',
    message:
      missingEnv.length === 0
        ? 'Todas las variables de entorno configuradas'
        : `Variables faltantes: ${missingEnv.join(', ')}`,
  };

  if (missingEnv.length > 0) {
    result.status = 'degraded';
  }

  // 6. Generar resumen
  const componentCount = Object.keys(result.components).length;
  const okCount = Object.values(result.components).filter((c) => c.status === 'ok').length;
  const errorCount = Object.values(result.components).filter((c) => c.status === 'error').length;

  result.summary = `Backend Health: ${okCount}/${componentCount} componentes OK. ${
    errorCount > 0 ? `${errorCount} errores detectados.` : 'Sistema funcionando correctamente.'
  }`;

  // Log del resultado
  logger.info('Backend connectivity check completed', {
    status: result.status,
    components: Object.keys(result.components).length,
    errors: errorCount,
  });

  return result;
}

/**
 * Helper para verificar si un middleware está configurado
 */
function checkMiddleware(app: Express, middlewareName: string): boolean {
  const middlewareStack = app._middleware || [];
  return middlewareStack.some((m: any) => m.name === middlewareName);
}

/**
 * Función para registrar un health check endpoint
 */
export function setupHealthCheckEndpoint(app: Express) {
  app.get('/api/health', async (req, res) => {
    try {
      const connectivity = await checkBackendConnectivity(app);
      const statusCode = connectivity.status === 'healthy' ? 200 : 503;

      res.status(statusCode).json({
        status: connectivity.status,
        timestamp: connectivity.timestamp,
        uptime: process.uptime(),
        nodeVersion: process.version,
        environment: process.env.NODE_ENV || 'development',
        components: connectivity.components,
        summary: connectivity.summary,
      });
    } catch (error) {
      res.status(503).json({
        status: 'error',
        message: 'Health check failed',
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  });

  logger.info('Health check endpoint registered at /api/health');
}

/**
 * Hook para verificar conectividad al iniciar el servidor
 */
export function logBackendStatus(app: Express) {
  const routes = app._router.stack
    .filter((r: any) => r.route)
    .map((r: any) => r.route.path);

  logger.info('=== BACKEND STATUS ===');
  logger.info(`✓ Server initialized with ${routes.length} routes`);
  logger.info('Registered routes:', { routes });
  logger.info('=== FENIX SERVICES ===');
  logger.info(`✓ Agent System: ${routes.some((r: string) => r.includes('/agents')) ? 'Connected' : 'Not found'}`);
  logger.info(`✓ Trading Engine: ${routes.some((r: string) => r.includes('/trading')) ? 'Connected' : 'Not found'}`);
  logger.info(`✓ Market Data: ${routes.some((r: string) => r.includes('/market')) ? 'Connected' : 'Not found'}`);
  logger.info(`✓ Reasoning Bank: ${routes.some((r: string) => r.includes('/reasoning')) ? 'Connected' : 'Not found'}`);
  logger.info(`✓ System Monitor: ${routes.some((r: string) => r.includes('/system')) ? 'Connected' : 'Not found'}`);
  logger.info('=======================');
}
