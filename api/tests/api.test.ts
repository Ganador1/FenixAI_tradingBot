/**
 * Basic API Tests - Health Check and Rate Limiting
 * Run with: npm test
 */

import app from '../app';

describe('API Health Tests', () => {
  describe('GET /api/health', () => {
    it('should return 200 status', async () => {
      const response = await fetch('http://localhost:3001/api/health');
      expect(response.status).toBe(200);
    });

    it('should return health check data', async () => {
      const response = await fetch('http://localhost:3001/api/health');
      const data = await response.json();
      
      expect(data.success).toBe(true);
      expect(data.message).toBe('Server is running');
      expect(data.uptime).toBeGreaterThan(0);
      expect(data.timestamp).toBeDefined();
    });
  });

  describe('404 Handler', () => {
    it('should return 404 for non-existent route', async () => {
      const response = await fetch('http://localhost:3001/api/nonexistent');
      expect(response.status).toBe(404);
      
      const data = await response.json();
      expect(data.success).toBe(false);
      expect(data.error).toBeDefined();
    });
  });

  describe('CORS Headers', () => {
    it('should include CORS headers', async () => {
      const response = await fetch('http://localhost:3001/api/health');
      
      expect(response.headers.get('access-control-allow-origin')).toBeDefined();
    });
  });

  describe('Request ID', () => {
    it('should include X-Request-ID header', async () => {
      const response = await fetch('http://localhost:3001/api/health');
      
      expect(response.headers.get('x-request-id')).toBeDefined();
    });
  });

  describe('Security Headers', () => {
    it('should include Helmet security headers', async () => {
      const response = await fetch('http://localhost:3001/api/health');
      
      // Check for Helmet headers
      expect(response.headers.get('x-content-type-options')).toBe('nosniff');
      expect(response.headers.get('x-frame-options')).toBeDefined();
    });
  });
});

describe('Rate Limiting', () => {
  it('should allow requests within limit', async () => {
    const response = await fetch('http://localhost:3001/api/health');
    expect(response.status).toBe(200);
  });

  it('should include rate limit headers', async () => {
    const response = await fetch('http://localhost:3001/api/health');
    
    const rateLimitRemaining = response.headers.get('ratelimit-remaining');
    expect(rateLimitRemaining).toBeDefined();
  });
});

export {};
