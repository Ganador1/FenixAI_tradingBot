/**
 * Vercel deploy entry handler, for serverless deployment, please don't modify this file
 */
import type { Request, Response } from 'express';
import app from './app.js';

export default function handler(req: Request, res: Response) {
  return app(req, res);
}
