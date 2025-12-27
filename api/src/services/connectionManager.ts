import { EventEmitter } from 'events';
import { createClient } from '@supabase/supabase-js';
import { createClient as createRedisClient } from 'redis';
import { WebSocket } from 'ws';
import winston from 'winston';

export interface ConnectionStatus {
  id: string;
  type: 'supabase' | 'redis' | 'websocket' | 'external-api';
  status: 'connected' | 'disconnected' | 'connecting' | 'error';
  lastHeartbeat: Date;
  errorCount: number;
  config: Record<string, any>;
  metrics: {
    latency: number;
    uptime: number;
    requests: number;
    errors: number;
  };
}

export interface ConnectionConfig {
  id: string;
  type: 'supabase' | 'redis' | 'websocket' | 'external-api';
  url?: string;
  apiKey?: string;
  reconnectInterval?: number;
  maxRetries?: number;
  timeout?: number;
  healthCheckInterval?: number;
  config?: Record<string, any>;
}

export class ConnectionManager extends EventEmitter {
  private connections: Map<string, ConnectionStatus> = new Map();
  private clients: Map<string, any> = new Map();
  private healthCheckIntervals: Map<string, NodeJS.Timeout> = new Map();
  private reconnectTimers: Map<string, NodeJS.Timeout> = new Map();
  private logger: winston.Logger;

  constructor() {
    super();
    this.logger = winston.createLogger({
      level: 'info',
      format: winston.format.combine(
        winston.format.timestamp(),
        winston.format.json()
      ),
      transports: [
        new winston.transports.Console(),
        new winston.transports.File({ filename: 'logs/connections.log' })
      ]
    });
  }

  async initializeConnection(config: ConnectionConfig): Promise<boolean> {
    try {
      this.logger.info(`Initializing connection: ${config.id} (${config.type})`);
      
      const connection: ConnectionStatus = {
        id: config.id,
        type: config.type,
        status: 'connecting',
        lastHeartbeat: new Date(),
        errorCount: 0,
        config: config.config || {},
        metrics: {
          latency: 0,
          uptime: 0,
          requests: 0,
          errors: 0
        }
      };

      this.connections.set(config.id, connection);
      this.emit('connection:status', connection);

      let client: any;
      let success = false;

      switch (config.type) {
        case 'supabase':
          success = await this.initializeSupabaseConnection(config);
          break;
        case 'redis':
          success = await this.initializeRedisConnection(config);
          break;
        case 'websocket':
          success = await this.initializeWebSocketConnection(config);
          break;
        case 'external-api':
          success = await this.initializeExternalApiConnection(config);
          break;
        default:
          throw new Error(`Unsupported connection type: ${config.type}`);
      }

      if (success) {
        connection.status = 'connected';
        connection.lastHeartbeat = new Date();
        this.logger.info(`Connection established: ${config.id}`);
        this.startHealthCheck(config.id);
        this.emit('connection:established', connection);
      }

      return success;
    } catch (error) {
      this.logger.error(`Failed to initialize connection ${config.id}:`, error);
      await this.handleConnectionError(config.id, error as Error);
      return false;
    }
  }

  private async initializeSupabaseConnection(config: ConnectionConfig): Promise<boolean> {
    if (!config.url || !config.apiKey) {
      throw new Error('Supabase connection requires url and apiKey');
    }

    const client = createClient(config.url, config.apiKey, {
      auth: {
        persistSession: true,
        autoRefreshToken: true
      }
    });

    // Test connection
    const { data, error } = await client.from('users').select('id').limit(1);
    if (error) {
      throw new Error(`Supabase connection test failed: ${error.message}`);
    }

    this.clients.set(config.id, client);
    return true;
  }

  private async initializeRedisConnection(config: ConnectionConfig): Promise<boolean> {
    if (!config.url) {
      throw new Error('Redis connection requires url');
    }

    const client = createRedisClient({
      url: config.url,
      socket: {
        reconnectStrategy: (retries) => Math.min(retries * 50, 500)
      }
    });

    client.on('error', (error) => {
      this.logger.error(`Redis connection error (${config.id}):`, error);
      this.handleConnectionError(config.id, error);
    });

    client.on('connect', () => {
      this.logger.info(`Redis connection established (${config.id})`);
    });

    await client.connect();
    this.clients.set(config.id, client);
    return true;
  }

  private async initializeWebSocketConnection(config: ConnectionConfig): Promise<boolean> {
    if (!config.url) {
      throw new Error('WebSocket connection requires url');
    }

    const ws = new WebSocket(config.url);
    
    ws.on('open', () => {
      this.logger.info(`WebSocket connection established (${config.id})`);
      const connection = this.connections.get(config.id);
      if (connection) {
        connection.status = 'connected';
        connection.lastHeartbeat = new Date();
        this.emit('connection:status', connection);
      }
    });

    ws.on('message', (data: Buffer) => {
      this.handleWebSocketMessage(config.id, data);
    });

    ws.on('error', (error) => {
      this.logger.error(`WebSocket error (${config.id}):`, error);
      this.handleConnectionError(config.id, error);
    });

    ws.on('close', (code, reason) => {
      this.logger.warn(`WebSocket connection closed (${config.id}): ${code} - ${reason}`);
      this.handleConnectionClose(config.id);
    });

    this.clients.set(config.id, ws);
    return true;
  }

  private async initializeExternalApiConnection(config: ConnectionConfig): Promise<boolean> {
    if (!config.url) {
      throw new Error('External API connection requires url');
    }

    // Test connection with a simple GET request
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), config.timeout || 5000);

    try {
      const response = await fetch(config.url, {
        signal: controller.signal,
        headers: config.apiKey ? { 'Authorization': `Bearer ${config.apiKey}` } : {}
      });

      clearTimeout(timeoutId);

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      this.clients.set(config.id, { url: config.url, apiKey: config.apiKey });
      return true;
    } catch (error) {
      clearTimeout(timeoutId);
      throw error;
    }
  }

  private handleWebSocketMessage(connectionId: string, data: Buffer) {
    try {
      const message = JSON.parse(data.toString());
      this.emit('websocket:message', { connectionId, message });
      
      // Update connection metrics
      const connection = this.connections.get(connectionId);
      if (connection) {
        connection.lastHeartbeat = new Date();
        connection.metrics.requests++;
      }
    } catch (error) {
      this.logger.error(`Failed to parse WebSocket message (${connectionId}):`, error);
    }
  }

  private async handleConnectionError(connectionId: string, error: Error) {
    const connection = this.connections.get(connectionId);
    if (!connection) return;

    connection.status = 'error';
    connection.errorCount++;
    connection.metrics.errors++;
    
    this.logger.error(`Connection error (${connectionId}): ${error.message}`);
    this.emit('connection:error', { connectionId, error: error.message });

    // Attempt reconnection if configured
    const config = connection.config;
    if (config.maxRetries && connection.errorCount <= config.maxRetries) {
      this.scheduleReconnection(connectionId, config);
    }
  }

  private handleConnectionClose(connectionId: string) {
    const connection = this.connections.get(connectionId);
    if (!connection) return;

    connection.status = 'disconnected';
    this.emit('connection:status', connection);
    
    // Stop health checks
    const healthCheckInterval = this.healthCheckIntervals.get(connectionId);
    if (healthCheckInterval) {
      clearInterval(healthCheckInterval);
      this.healthCheckIntervals.delete(connectionId);
    }
  }

  private scheduleReconnection(connectionId: string, config: Record<string, any>) {
    const existingTimer = this.reconnectTimers.get(connectionId);
    if (existingTimer) {
      clearTimeout(existingTimer);
    }

    const reconnectInterval = config.reconnectInterval || 5000;
    
    const timer = setTimeout(async () => {
      this.logger.info(`Attempting reconnection for ${connectionId}`);
      
      const connectionConfig: ConnectionConfig = {
        id: connectionId,
        type: this.connections.get(connectionId)?.type || 'external-api',
        ...config
      };

      await this.initializeConnection(connectionConfig);
      this.reconnectTimers.delete(connectionId);
    }, reconnectInterval);

    this.reconnectTimers.set(connectionId, timer);
  }

  private startHealthCheck(connectionId: string) {
    const connection = this.connections.get(connectionId);
    if (!connection) return;

    const healthCheckInterval = connection.config.healthCheckInterval || 30000;

    const interval = setInterval(async () => {
      try {
        await this.performHealthCheck(connectionId);
      } catch (error) {
        this.logger.error(`Health check failed (${connectionId}):`, error);
        await this.handleConnectionError(connectionId, error as Error);
      }
    }, healthCheckInterval);

    this.healthCheckIntervals.set(connectionId, interval);
  }

  private async performHealthCheck(connectionId: string): Promise<void> {
    const connection = this.connections.get(connectionId);
    const client = this.clients.get(connectionId);

    if (!connection || !client) {
      throw new Error(`Connection or client not found: ${connectionId}`);
    }

    const startTime = Date.now();

    switch (connection.type) {
      case 'supabase':
        const { error } = await client.from('users').select('id').limit(1);
        if (error) throw new Error(`Supabase health check failed: ${error.message}`);
        break;

      case 'redis':
        await client.ping();
        break;

      case 'websocket':
        if (client.readyState !== WebSocket.OPEN) {
          throw new Error('WebSocket not open');
        }
        break;

      case 'external-api':
        const response = await fetch(client.url, {
          method: 'HEAD',
          headers: client.apiKey ? { 'Authorization': `Bearer ${client.apiKey}` } : {}
        });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        break;
    }

    const latency = Date.now() - startTime;
    connection.lastHeartbeat = new Date();
    connection.metrics.latency = latency;
    
    this.emit('connection:heartbeat', { connectionId, latency });
  }

  async disconnect(connectionId: string): Promise<void> {
    const connection = this.connections.get(connectionId);
    if (!connection) return;

    this.logger.info(`Disconnecting connection: ${connectionId}`);

    // Stop health checks and reconnection timers
    const healthCheckInterval = this.healthCheckIntervals.get(connectionId);
    if (healthCheckInterval) {
      clearInterval(healthCheckInterval);
      this.healthCheckIntervals.delete(connectionId);
    }

    const reconnectTimer = this.reconnectTimers.get(connectionId);
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      this.reconnectTimers.delete(connectionId);
    }

    // Close client connection
    const client = this.clients.get(connectionId);
    if (client) {
      try {
        switch (connection.type) {
          case 'websocket':
            if (client.readyState === WebSocket.OPEN) {
              client.close(1000, 'Normal closure');
            }
            break;
          case 'redis':
            await client.disconnect();
            break;
        }
      } catch (error) {
        this.logger.error(`Error closing client connection (${connectionId}):`, error);
      }
      
      this.clients.delete(connectionId);
    }

    connection.status = 'disconnected';
    this.emit('connection:status', connection);
    this.connections.delete(connectionId);
  }

  getConnectionStatus(connectionId: string): ConnectionStatus | undefined {
    return this.connections.get(connectionId);
  }

  getAllConnections(): ConnectionStatus[] {
    return Array.from(this.connections.values());
  }

  getActiveConnections(): ConnectionStatus[] {
    return Array.from(this.connections.values())
      .filter(conn => conn.status === 'connected');
  }

  async sendWebSocketMessage(connectionId: string, message: any): Promise<void> {
    const connection = this.connections.get(connectionId);
    const client = this.clients.get(connectionId);

    if (!connection || connection.type !== 'websocket') {
      throw new Error(`WebSocket connection not found: ${connectionId}`);
    }

    if (!client || client.readyState !== WebSocket.OPEN) {
      throw new Error(`WebSocket not open: ${connectionId}`);
    }

    client.send(JSON.stringify(message));
  }

  async shutdown(): Promise<void> {
    this.logger.info('Shutting down connection manager');
    
    const connectionIds = Array.from(this.connections.keys());
    await Promise.all(connectionIds.map(id => this.disconnect(id)));
    
    this.removeAllListeners();
  }
}

export const connectionManager = new ConnectionManager();