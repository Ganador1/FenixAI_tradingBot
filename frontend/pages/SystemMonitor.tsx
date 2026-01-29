import React, { useState, useEffect } from 'react';
import { LineChart, Line, AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, PieChart, Pie, Cell } from 'recharts';
import { 
  AlertTriangle, 
  CheckCircle, 
  XCircle, 
  Clock, 
  Server, 
  Wifi, 
  HardDrive, 
  Cpu, 
  MemoryStick,
  RefreshCw,
} from 'lucide-react';
// import { useAuthStore } from '../stores/authStore';
import { useSystemStore } from '../stores/systemStore';
import { Card, CardHeader, CardTitle, CardContent } from '../components/ui/Card';
import { Button } from '../components/ui/Button';
// import { Select } from '../components/ui/Select';
import { Badge } from '../components/ui/Badge';
import { Alert, AlertTitle, AlertDescription } from '../components/ui/Alert';

interface SystemMetrics {
  timestamp: string;
  cpu: {
    usage: number;
    cores: number;
    load_average: number[];
  };
  memory: {
    total: number;
    used: number;
    free: number;
    percentage: number;
  };
  disk: {
    total: number;
    used: number;
    free: number;
    percentage: number;
  };
  network: {
    bytes_in: number;
    bytes_out: number;
    packets_in: number;
    packets_out: number;
  };
  process: {
    uptime: number;
    pid: number;
    version: string;
    node_version: string;
  };
}

interface ComponentHealth {
  component: string;
  status: 'healthy' | 'warning' | 'error' | 'unknown';
  message: string;
  last_check: string;
  response_time?: number;
}

/*
interface SystemAlert {
  id: string;
  type: 'info' | 'warning' | 'error';
  component: string;
  message: string;
  timestamp: string;
  resolved: boolean;
  auto_resolved: boolean;
}
*/

interface ConnectionStatus {
  service: string;
  status: 'connected' | 'disconnected' | 'connecting' | 'error';
  last_ping: string;
  reconnect_attempts: number;
  error_count: number;
}

const STATUS_COLORS = {
  healthy: 'text-green-600 bg-green-100',
  warning: 'text-yellow-600 bg-yellow-100',
  error: 'text-red-600 bg-red-100',
  unknown: 'text-gray-600 bg-gray-100'
};

/*
const ALERT_COLORS = {
  info: 'text-blue-600 bg-blue-100',
  warning: 'text-yellow-600 bg-yellow-100',
  error: 'text-red-600 bg-red-100'
};
*/

export const SystemMonitor: React.FC = () => {
  // const { user } = useAuthStore();
  const { metrics, alerts, fetchSystemStatus, fetchAlerts } = useSystemStore();
  
  const currentMetrics = metrics || {
    cpu: 0,
    memory: 0,
    disk: 0,
    network: 0,
    process: 0,
    timestamp: new Date().toISOString()
  };
  const [selectedTimeframe] = useState<string>('1h');
  const [autoRefresh, setAutoRefresh] = useState<boolean>(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [metricsHistory, setMetricsHistory] = useState<SystemMetrics[]>([]);
  const [componentHealth, setComponentHealth] = useState<ComponentHealth[]>([]);
  const [connectionStatuses, setConnectionStatuses] = useState<ConnectionStatus[]>([]);

  useEffect(() => {
    fetchSystemData();
    
    if (autoRefresh) {
      const interval = setInterval(fetchSystemData, 30000); // Refresh every 30 seconds
      return () => clearInterval(interval);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRefresh, selectedTimeframe]);

  const fetchSystemData = async () => {
    try {
      setLoading(true);
      setError(null);

      await Promise.all([
        fetchSystemStatus(),
        fetchAlerts()
      ]);

      // Fetch component health
      const healthResponse = await fetch('/api/system/health');
      if (healthResponse.ok) {
        const healthData = await healthResponse.json();
        setComponentHealth(healthData.components);
      }

      // Fetch connection status
      const connectionResponse = await fetch('/api/system/connections');
      if (connectionResponse.ok) {
        const connectionData = await connectionResponse.json();
        setConnectionStatuses(connectionData.connections);
      }

      // Fetch metrics history
      const historyResponse = await fetch(`/api/system/metrics/history?timeframe=${selectedTimeframe}`);
      if (historyResponse.ok) {
        const historyData = await historyResponse.json();
        setMetricsHistory(historyData.metrics);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch system data');
    } finally {
      setLoading(false);
    }
  };

  /*
  const formatBytes = (bytes: number): string => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };
  */

  const formatUptime = (seconds: number): string => {
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    
    if (days > 0) return `${days}d ${hours}h ${minutes}m`;
    if (hours > 0) return `${hours}h ${minutes}m`;
    return `${minutes}m`;
  };

  const getStatusBadge = (status: string) => {
    const colorClass = STATUS_COLORS[status as keyof typeof STATUS_COLORS] || STATUS_COLORS.unknown;
    return (
      <Badge variant="outline" className={`${colorClass} capitalize`}>
        {status}
      </Badge>
    );
  };

  const getConnectionStatusBadge = (status: string) => {
    switch (status) {
      case 'connected':
        return <Badge variant="success">Connected</Badge>;
      case 'disconnected':
        return <Badge variant="error">Disconnected</Badge>;
      case 'connecting':
        return <Badge variant="warning">Connecting</Badge>;
      case 'error':
        return <Badge variant="error">Error</Badge>;
      default:
        return <Badge variant="default">Unknown</Badge>;
    }
  };

  const getAlertIcon = (type: string) => {
    switch (type) {
      case 'error':
        return <XCircle className="h-4 w-4 text-red-600" />;
      case 'warning':
        return <AlertTriangle className="h-4 w-4 text-yellow-600" />;
      default:
        return <CheckCircle className="h-4 w-4 text-blue-600" />;
    }
  };

  const prepareCPUChartData = () => {
    return metricsHistory.map(metric => ({
      timestamp: new Date(metric.timestamp).toLocaleTimeString(),
      usage: metric.cpu.usage,
      load: metric.cpu.load_average[0] * 10 // Scale for visibility
    }));
  };

  const prepareMemoryChartData = () => {
    return metricsHistory.map(metric => ({
      timestamp: new Date(metric.timestamp).toLocaleTimeString(),
      used: metric.memory.used / 1024 / 1024 / 1024, // Convert to GB
      free: metric.memory.free / 1024 / 1024 / 1024
    }));
  };

  const prepareNetworkChartData = () => {
    return metricsHistory.map(metric => ({
      timestamp: new Date(metric.timestamp).toLocaleTimeString(),
      bytes_in: metric.network.bytes_in / 1024 / 1024, // Convert to MB
      bytes_out: metric.network.bytes_out / 1024 / 1024
    }));
  };

  const prepareDiskChartData = () => {
    return metricsHistory.map(metric => ({
      timestamp: new Date(metric.timestamp).toLocaleTimeString(),
      used: metric.disk.used / 1024 / 1024 / 1024, // Convert to GB
      free: metric.disk.free / 1024 / 1024 / 1024
    }));
  };

  const prepareComponentHealthData = () => {
    const statusCounts = componentHealth.reduce((acc, component) => {
      acc[component.status] = (acc[component.status] || 0) + 1;
      return acc;
    }, {} as Record<string, number>);

    return Object.entries(statusCounts).map(([status, count]) => ({
      name: status.charAt(0).toUpperCase() + status.slice(1),
      value: count,
      color: status === 'healthy' ? '#10b981' : status === 'warning' ? '#f59e0b' : '#ef4444'
    }));
  };

  const activeAlerts = alerts.filter(alert => !alert.resolved);
  const criticalAlerts = activeAlerts.filter(alert => alert.type === 'error');
  const warningAlerts = activeAlerts.filter(alert => alert.type === 'warning');

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {error && (
        <Alert variant="error" className="mb-4">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>System Error</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* System Overview */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">System Monitor</h1>
        <div className="flex items-center space-x-2">
          <Button
            variant="outline"
            size="sm"
            onClick={fetchSystemData}
            className="flex items-center space-x-1"
          >
            <RefreshCw className="h-4 w-4" />
            <span>Refresh</span>
          </Button>
          <Button
            variant={autoRefresh ? 'secondary' : 'outline'}
            size="sm"
            onClick={() => setAutoRefresh(!autoRefresh)}
          >
            Auto Refresh: {autoRefresh ? 'ON' : 'OFF'}
          </Button>
        </div>
      </div>

      {/* Alert Summary */}
      {activeAlerts.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <Card className="border-red-200 bg-red-50">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium text-red-800">Critical Alerts</CardTitle>
              <XCircle className="h-4 w-4 text-red-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-red-900">{criticalAlerts.length}</div>
            </CardContent>
          </Card>

          <Card className="border-yellow-200 bg-yellow-50">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium text-yellow-800">Warning Alerts</CardTitle>
              <AlertTriangle className="h-4 w-4 text-yellow-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-yellow-900">{warningAlerts.length}</div>
            </CardContent>
          </Card>

          <Card className="border-green-200 bg-green-50">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium text-green-800">Healthy Components</CardTitle>
              <CheckCircle className="h-4 w-4 text-green-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-green-900">
                {componentHealth.filter(c => c.status === 'healthy').length}
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Current Metrics */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">CPU Usage</CardTitle>
            <Cpu className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{currentMetrics.cpu.toFixed(1)}%</div>
              <p className="text-xs text-gray-500">
                CPU Usage
              </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Memory Usage</CardTitle>
            <MemoryStick className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {currentMetrics.memory.toFixed(1)}%
            </div>
            <p className="text-xs text-gray-500">
              Memory Usage
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Disk Usage</CardTitle>
            <HardDrive className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {currentMetrics.disk.toFixed(1)}%
            </div>
            <p className="text-xs text-gray-500">
              Disk Usage
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Uptime</CardTitle>
            <Clock className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {formatUptime(currentMetrics.process)}
            </div>
            <p className="text-xs text-gray-500">
              Process Uptime
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Connection Status */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between">
            <span>Connection Status</span>
            <Wifi className="h-5 w-5" />
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {connectionStatuses.map((connection) => (
              <div key={connection.service} className="border rounded-lg p-4">
                <div className="flex justify-between items-center mb-2">
                  <h4 className="font-semibold capitalize">{connection.service}</h4>
                  {getConnectionStatusBadge(connection.status)}
                </div>
                <div className="space-y-1 text-sm">
                  <div className="flex justify-between">
                    <span className="text-gray-500">Last Ping:</span>
                    <span>{new Date(connection.last_ping).toLocaleTimeString()}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">Reconnects:</span>
                    <span>{connection.reconnect_attempts}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">Errors:</span>
                    <span>{connection.error_count}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Component Health */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <Card>
          <CardHeader>
            <CardTitle>Component Health Distribution</CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={250}>
              <PieChart>
                <Pie
                  data={prepareComponentHealthData()}
                  cx="50%"
                  cy="50%"
                  labelLine={false}
                  label={({ name, value }) => `${name}: ${value}`}
                  outerRadius={80}
                  fill="#8884d8"
                  dataKey="value"
                >
                  {prepareComponentHealthData().map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={entry.color} />
                  ))}
                </Pie>
                <Tooltip />
              </PieChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Component Status</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {componentHealth.map((component) => (
                <div key={component.component} className="border rounded-lg p-3">
                  <div className="flex justify-between items-center mb-2">
                    <h4 className="font-semibold capitalize">{component.component}</h4>
                    {getStatusBadge(component.status)}
                  </div>
                  <p className="text-sm text-gray-600 mb-2">{component.message}</p>
                  <div className="flex justify-between items-center text-xs text-gray-500">
                    <span>Last Check: {new Date(component.last_check).toLocaleTimeString()}</span>
                    {component.response_time && (
                      <span>Response: {component.response_time}ms</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Performance Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card>
          <CardHeader>
            <CardTitle>CPU Usage Trend</CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={250}>
              <LineChart data={prepareCPUChartData()}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="timestamp" />
                <YAxis />
                <Tooltip />
                <Legend />
                <Line type="monotone" dataKey="usage" stroke="#3b82f6" name="CPU Usage %" />
                <Line type="monotone" dataKey="load" stroke="#10b981" name="Load Average (x10)" />
              </LineChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Memory Usage Trend</CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={250}>
              <AreaChart data={prepareMemoryChartData()}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="timestamp" />
                <YAxis />
                <Tooltip />
                <Legend />
                <Area type="monotone" dataKey="used" stackId="1" stroke="#3b82f6" fill="#3b82f6" fillOpacity={0.6} name="Used (GB)" />
                <Area type="monotone" dataKey="free" stackId="1" stroke="#10b981" fill="#10b981" fillOpacity={0.6} name="Free (GB)" />
              </AreaChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Network Activity</CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={250}>
              <AreaChart data={prepareNetworkChartData()}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="timestamp" />
                <YAxis />
                <Tooltip />
                <Legend />
                <Area type="monotone" dataKey="bytes_in" stackId="1" stroke="#3b82f6" fill="#3b82f6" fillOpacity={0.6} name="Inbound (MB)" />
                <Area type="monotone" dataKey="bytes_out" stackId="1" stroke="#ef4444" fill="#ef4444" fillOpacity={0.6} name="Outbound (MB)" />
              </AreaChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Disk Usage Trend</CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={250}>
              <AreaChart data={prepareDiskChartData()}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="timestamp" />
                <YAxis />
                <Tooltip />
                <Legend />
                <Area type="monotone" dataKey="used" stackId="1" stroke="#f59e0b" fill="#f59e0b" fillOpacity={0.6} name="Used (GB)" />
                <Area type="monotone" dataKey="free" stackId="1" stroke="#10b981" fill="#10b981" fillOpacity={0.6} name="Free (GB)" />
              </AreaChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </div>

      {/* Active Alerts */}
      {activeAlerts.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Active Alerts</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {activeAlerts.map((alert) => (
                <div key={alert.id} className={`border rounded-lg p-4 ${alert.type === 'error' ? 'border-red-200 bg-red-50' : 'border-yellow-200 bg-yellow-50'}`}>
                  <div className="flex items-start space-x-3">
                    {getAlertIcon(alert.type)}
                    <div className="flex-1">
                      <div className="flex justify-between items-start mb-1">
                        <h4 className="font-semibold capitalize">{alert.component}</h4>
                        <span className="text-xs text-gray-500">
                          {new Date(alert.created_at).toLocaleTimeString()}
                        </span>
                      </div>
                      <p className="text-sm text-gray-700">{alert.message}</p>
                      {alert.resolved && (
                        <Badge variant="outline" className="mt-2 text-xs">
                          Auto-resolved
                        </Badge>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* System Info */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between">
            <span>System Information</span>
            <Server className="h-5 w-5" />
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <div className="flex justify-between">
                <span className="text-sm font-medium text-gray-600">Process Version:</span>
                <span className="text-sm">System v1.0.0</span>
              </div>
              <div className="flex justify-between">
                <span className="text-sm font-medium text-gray-600">Node.js Version:</span>
                <span className="text-sm">v18.0.0</span>
              </div>
              <div className="flex justify-between">
                <span className="text-sm font-medium text-gray-600">CPU Cores:</span>
                <span className="text-sm">4 Cores</span>
              </div>
            </div>
            <div className="space-y-2">
              <div className="flex justify-between">
                <span className="text-sm font-medium text-gray-600">Total Memory:</span>
                <span className="text-sm">8 GB</span>
              </div>
              <div className="flex justify-between">
                <span className="text-sm font-medium text-gray-600">Total Disk:</span>
                <span className="text-sm">100 GB</span>
              </div>
              <div className="flex justify-between">
                <span className="text-sm font-medium text-gray-600">Network I/O:</span>
                <span className="text-sm">
                  {currentMetrics.network.toFixed(1)}%
                </span>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
};