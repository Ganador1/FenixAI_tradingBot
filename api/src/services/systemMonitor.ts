import { EventEmitter } from 'events';
import { connectionManager, ConnectionStatus } from './connectionManager';
import winston from 'winston';
import os from 'os';
import { exec } from 'child_process';
import util from 'util';
import { promises as fs } from 'fs';

export interface SystemMetrics {
  cpu: {
    usage: number;
    cores: number;
    loadAverage: number[];
  };
  memory: {
    total: number;
    used: number;
    free: number;
    usage: number;
  };
  disk: {
    total: number;
    used: number;
    free: number;
    usage: number;
  };
  network: {
    bytesIn: number;
    bytesOut: number;
    packetsIn: number;
    packetsOut: number;
  };
  process: {
    uptime: number;
    memory: number;
    cpu: number;
    pid: number;
  };
}

export interface SystemAlert {
  id: string;
  type: 'performance' | 'connection' | 'error' | 'warning' | 'info';
  severity: 'critical' | 'warning' | 'info';
  title: string;
  message: string;
  component: string;
  timestamp: Date;
  resolved: boolean;
  metadata?: Record<string, any>;
}

export interface ComponentHealth {
  component: string;
  status: 'healthy' | 'degraded' | 'unhealthy' | 'unknown';
  lastCheck: Date;
  metrics: Record<string, any>;
  alerts: SystemAlert[];
}

export class SystemMonitor extends EventEmitter {
  private metrics: SystemMetrics;
  private alerts: SystemAlert[] = [];
  private componentHealth: Map<string, ComponentHealth> = new Map();
  private metricsInterval?: NodeJS.Timeout;
  private alertsInterval?: NodeJS.Timeout;
  private logger: winston.Logger;
  private startTime: Date;

  constructor() {
    super();
    this.startTime = new Date();
    this.logger = winston.createLogger({
      level: 'info',
      format: winston.format.combine(
        winston.format.timestamp(),
        winston.format.json()
      ),
      transports: [
        new winston.transports.Console(),
        new winston.transports.File({ filename: 'logs/system-monitor.log' })
      ]
    });

    this.metrics = this.initializeMetrics();
    this.initializeConnectionListeners();
  }

  private initializeMetrics(): SystemMetrics {
    return {
      cpu: {
        usage: 0,
        cores: os.cpus().length,
        loadAverage: os.loadavg()
      },
      memory: {
        total: os.totalmem(),
        used: 0,
        free: 0,
        usage: 0
      },
      disk: {
        total: 0,
        used: 0,
        free: 0,
        usage: 0
      },
      network: {
        bytesIn: 0,
        bytesOut: 0,
        packetsIn: 0,
        packetsOut: 0
      },
      process: {
        uptime: 0,
        memory: 0,
        cpu: 0,
        pid: process.pid
      }
    };
  }

  private initializeConnectionListeners(): void {
    connectionManager.on('connection:status', (status: ConnectionStatus) => {
      this.updateComponentHealth('connections', {
        status: status.status === 'connected' ? 'healthy' : 
                status.status === 'error' ? 'unhealthy' : 'degraded',
        lastCheck: new Date(),
        metrics: {
          connectionId: status.id,
          connectionType: status.type,
          errorCount: status.errorCount,
          latency: status.metrics.latency
        }
      });

      if (status.status === 'error') {
        this.createAlert({
          type: 'connection',
          severity: 'warning',
          title: 'Connection Error',
          message: `Connection ${status.id} (${status.type}) is experiencing errors`,
          component: 'connection-manager',
          metadata: { connection: status }
        });
      }
    });

    connectionManager.on('connection:error', ({ connectionId, error }) => {
      this.createAlert({
        type: 'connection',
        severity: 'critical',
        title: 'Connection Failed',
        message: `Connection ${connectionId} failed: ${error}`,
        component: 'connection-manager',
        metadata: { connectionId, error }
      });
    });

    connectionManager.on('connection:heartbeat', ({ connectionId, latency }) => {
      if (latency > 1000) {
        this.createAlert({
          type: 'performance',
          severity: 'warning',
          title: 'High Latency Detected',
          message: `Connection ${connectionId} latency is ${latency}ms`,
          component: 'connection-manager',
          metadata: { connectionId, latency }
        });
      }
    });
  }

  startMonitoring(intervalMs: number = 30000): void {
    if (this.metricsInterval) {
      clearInterval(this.metricsInterval);
    }

    if (this.alertsInterval) {
      clearInterval(this.alertsInterval);
    }

    this.metricsInterval = setInterval(() => {
      this.collectMetrics();
    }, intervalMs);

    this.alertsInterval = setInterval(() => {
      this.processAlerts();
    }, Math.max(intervalMs / 2, 10000));

    this.logger.info('System monitoring started');
    this.emit('monitoring:started', { interval: intervalMs });
  }

  stopMonitoring(): void {
    if (this.metricsInterval) {
      clearInterval(this.metricsInterval);
      this.metricsInterval = undefined;
    }

    if (this.alertsInterval) {
      clearInterval(this.alertsInterval);
      this.alertsInterval = undefined;
    }

    this.logger.info('System monitoring stopped');
    this.emit('monitoring:stopped');
  }

  private async collectMetrics(): Promise<void> {
    try {
      await Promise.all([
        this.collectCpuMetrics(),
        this.collectMemoryMetrics(),
        this.collectDiskMetrics(),
        this.collectNetworkMetrics(),
        this.collectProcessMetrics()
      ]);

      this.checkThresholds();
      this.emit('metrics:updated', this.metrics);
    } catch (error) {
      this.logger.error('Failed to collect system metrics:', error);
      this.createAlert({
        type: 'error',
        severity: 'warning',
        title: 'Metrics Collection Failed',
        message: 'Unable to collect system metrics',
        component: 'system-monitor',
        metadata: { error: (error as Error).message }
      });
    }
  }

  private async collectCpuMetrics(): Promise<void> {
    const cpus = os.cpus();
    
    let totalIdle = 0;
    let totalTick = 0;

    cpus.forEach(cpu => {
      for (const type in cpu.times) {
        totalTick += cpu.times[type as keyof typeof cpu.times];
      }
      totalIdle += cpu.times.idle;
    });

    const cpuUsage = 100 - ~~(100 * totalIdle / totalTick);
    
    this.metrics.cpu = {
      usage: cpuUsage,
      cores: cpus.length,
      loadAverage: os.loadavg()
    };
  }

  private async collectMemoryMetrics(): Promise<void> {
    const totalMem = os.totalmem();
    const freeMem = os.freemem();
    const usedMem = totalMem - freeMem;

    this.metrics.memory = {
      total: totalMem,
      used: usedMem,
      free: freeMem,
      usage: (usedMem / totalMem) * 100
    };
  }

  private async collectDiskMetrics(): Promise<void> {
    try {
      const execAsync = util.promisify(exec);

      const { stdout } = await execAsync('df -k /');
      const lines = stdout.trim().split('\n');
      const data = lines[1].split(/\s+/);

      const total = parseInt(data[1]) * 1024;
      const used = parseInt(data[2]) * 1024;
      const free = parseInt(data[3]) * 1024;

      this.metrics.disk = {
        total,
        used,
        free,
        usage: (used / total) * 100
      };
    } catch (error) {
      this.logger.warn('Failed to collect disk metrics:', error);
    }
  }

  private async collectNetworkMetrics(): Promise<void> {
    try {
      const netStats = await fs.readFile('/proc/net/dev', 'utf8');
      
      const lines = netStats.split('\n');
      let totalBytesIn = 0;
      let totalBytesOut = 0;
      let totalPacketsIn = 0;
      let totalPacketsOut = 0;

      lines.forEach(line => {
        if (line.includes('eth') || line.includes('wlan')) {
          const data = line.split(/\s+/);
          if (data.length > 9) {
            totalBytesIn += parseInt(data[1]) || 0;
            totalPacketsIn += parseInt(data[2]) || 0;
            totalBytesOut += parseInt(data[9]) || 0;
            totalPacketsOut += parseInt(data[10]) || 0;
          }
        }
      });

      this.metrics.network = {
        bytesIn: totalBytesIn,
        bytesOut: totalBytesOut,
        packetsIn: totalPacketsIn,
        packetsOut: totalPacketsOut
      };
    } catch (error) {
      // Fallback for non-Linux systems
      this.metrics.network = {
        bytesIn: 0,
        bytesOut: 0,
        packetsIn: 0,
        packetsOut: 0
      };
    }
  }

  private async collectProcessMetrics(): Promise<void> {
    const memoryUsage = process.memoryUsage();
    const uptime = Date.now() - this.startTime.getTime();

    this.metrics.process = {
      uptime: uptime,
      memory: memoryUsage.rss,
      cpu: this.metrics.cpu.usage,
      pid: process.pid
    };
  }

  private checkThresholds(): void {
    const thresholds = {
      cpu: 80,
      memory: 85,
      disk: 90
    };

    if (this.metrics.cpu.usage > thresholds.cpu) {
      this.createAlert({
        type: 'performance',
        severity: 'warning',
        title: 'High CPU Usage',
        message: `CPU usage is at ${this.metrics.cpu.usage.toFixed(1)}%`,
        component: 'system-monitor',
        metadata: { cpu: this.metrics.cpu }
      });
    }

    if (this.metrics.memory.usage > thresholds.memory) {
      this.createAlert({
        type: 'performance',
        severity: 'warning',
        title: 'High Memory Usage',
        message: `Memory usage is at ${this.metrics.memory.usage.toFixed(1)}%`,
        component: 'system-monitor',
        metadata: { memory: this.metrics.memory }
      });
    }

    if (this.metrics.disk.usage > thresholds.disk) {
      this.createAlert({
        type: 'performance',
        severity: 'critical',
        title: 'High Disk Usage',
        message: `Disk usage is at ${this.metrics.disk.usage.toFixed(1)}%`,
        component: 'system-monitor',
        metadata: { disk: this.metrics.disk }
      });
    }
  }

  private createAlert(alert: Omit<SystemAlert, 'id' | 'timestamp' | 'resolved'>): void {
    const fullAlert: SystemAlert = {
      ...alert,
      id: this.generateAlertId(),
      timestamp: new Date(),
      resolved: false
    };

    this.alerts.push(fullAlert);
    this.logger.warn(`Alert created: ${fullAlert.title} - ${fullAlert.message}`);
    this.emit('alert:created', fullAlert);
  }

  private processAlerts(): void {
    // Remove resolved alerts older than 24 hours
    const cutoffTime = new Date(Date.now() - 24 * 60 * 60 * 1000);
    this.alerts = this.alerts.filter(alert => 
      !alert.resolved || alert.timestamp > cutoffTime
    );

    // Auto-resolve alerts based on conditions
    this.alerts.forEach(alert => {
      if (!alert.resolved && this.shouldAutoResolveAlert(alert)) {
        alert.resolved = true;
        this.emit('alert:resolved', alert);
      }
    });
  }

  private shouldAutoResolveAlert(alert: SystemAlert): boolean {
    switch (alert.type) {
      case 'performance':
        if (alert.title.includes('CPU Usage')) {
          return this.metrics.cpu.usage < 70;
        }
        if (alert.title.includes('Memory Usage')) {
          return this.metrics.memory.usage < 75;
        }
        if (alert.title.includes('Disk Usage')) {
          return this.metrics.disk.usage < 80;
        }
        break;
      
      case 'connection':
        if (alert.component === 'connection-manager') {
          const connectionId = alert.metadata?.connection?.id;
          if (connectionId) {
            const status = connectionManager.getConnectionStatus(connectionId);
            return status?.status === 'connected';
          }
        }
        break;
    }

    return false;
  }

  private generateAlertId(): string {
    return `alert_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
  }

  updateComponentHealth(component: string, health: Partial<ComponentHealth>): void {
    const existing = this.componentHealth.get(component) || {
      component,
      status: 'unknown',
      lastCheck: new Date(),
      metrics: {},
      alerts: []
    };

    this.componentHealth.set(component, {
      ...existing,
      ...health,
      lastCheck: new Date()
    });

    this.emit('component:health', { component, health: this.componentHealth.get(component) });
  }

  getSystemMetrics(): SystemMetrics {
    return { ...this.metrics };
  }

  getSystemAlerts(): SystemAlert[] {
    return [...this.alerts];
  }

  getComponentHealth(component?: string): ComponentHealth | ComponentHealth[] {
    if (component) {
      return this.componentHealth.get(component) || {
        component,
        status: 'unknown',
        lastCheck: new Date(),
        metrics: {},
        alerts: []
      };
    }
    
    return Array.from(this.componentHealth.values());
  }

  getSystemStatus(): {
    status: 'healthy' | 'degraded' | 'unhealthy';
    uptime: number;
    components: ComponentHealth[];
    activeAlerts: SystemAlert[];
    metrics: SystemMetrics;
  } {
    const components = this.getComponentHealth() as ComponentHealth[];
    const activeAlerts = this.getSystemAlerts().filter(alert => !alert.resolved);
    
    const unhealthyComponents = components.filter(c => c.status === 'unhealthy').length;
    const degradedComponents = components.filter(c => c.status === 'degraded').length;
    const criticalAlerts = activeAlerts.filter(a => a.severity === 'critical').length;

    let status: 'healthy' | 'degraded' | 'unhealthy' = 'healthy';
    
    if (unhealthyComponents > 0 || criticalAlerts > 0) {
      status = 'unhealthy';
    } else if (degradedComponents > 0 || activeAlerts.length > 0) {
      status = 'degraded';
    }

    return {
      status,
      uptime: Date.now() - this.startTime.getTime(),
      components,
      activeAlerts,
      metrics: this.metrics
    };
  }

  resolveAlert(alertId: string): boolean {
    const alert = this.alerts.find(a => a.id === alertId);
    if (alert && !alert.resolved) {
      alert.resolved = true;
      alert.metadata = { ...alert.metadata, resolvedAt: new Date() };
      this.emit('alert:resolved', alert);
      return true;
    }
    return false;
  }
}

export const systemMonitor = new SystemMonitor();