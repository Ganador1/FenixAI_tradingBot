import { Request, Response, NextFunction } from 'express';
import winston from 'winston';

// Logger setup
const logger = winston.createLogger({
  level: process.env.LOG_LEVEL || 'info',
  format: winston.format.json(),
  defaultMeta: { service: 'trading-api' },
  transports: [
    new winston.transports.Console({
      format: winston.format.simple(),
    }),
  ],
});

export class AppError extends Error {
  constructor(
    public statusCode: number,
    public message: string,
    public isOperational: boolean = true,
  ) {
    super(message);
    Object.setPrototypeOf(this, AppError.prototype);
  }
}

export const errorHandler = (
  error: Error | AppError,
  req: Request,
  res: Response,
  next: NextFunction,
): void => {
  if (error instanceof AppError) {
    logger.error({
      status: error.statusCode,
      message: error.message,
      path: req.path,
      method: req.method,
    });

    res.status(error.statusCode).json({
      success: false,
      error: error.message,
      requestId: req.id || 'unknown',
    });
  } else {
    logger.error({
      status: 500,
      message: error.message,
      stack: error.stack,
      path: req.path,
      method: req.method,
    });

    res.status(500).json({
      success: false,
      error: process.env.NODE_ENV === 'production'
        ? 'Internal server error'
        : error.message,
      requestId: req.id || 'unknown',
    });
  }
};

export const asyncHandler = (
  fn: (req: Request, res: Response, next: NextFunction) => Promise<any>,
) => {
  return (req: Request, res: Response, next: NextFunction) => {
    Promise.resolve(fn(req, res, next)).catch(next);
  };
};

export default logger;
