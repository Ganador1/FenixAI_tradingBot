import { Router } from 'express';
import { connectionManager } from '../services/connectionManager';
import { systemMonitor } from '../services/systemMonitor';

const router = Router();

// Get system status
router.get('/status', (req, res) => {
  try {
    const systemStatus = systemMonitor.getSystemStatus();
    const connections = connectionManager.getAllConnections();
    
    res.json({
      success: true,
      data: {
        system: systemStatus,
        connections: connections
      }
    });
  } catch (error) {
    res.status(500).json({
      success: false,
      error: 'Failed to get system status',
      details: (error as Error).message
    });
  }
});

// Get system metrics
router.get('/metrics', (req, res) => {
  try {
    const metrics = systemMonitor.getSystemMetrics();
    
    res.json({
      success: true,
      data: metrics
    });
  } catch (error) {
    res.status(500).json({
      success: false,
      error: 'Failed to get system metrics',
      details: (error as Error).message
    });
  }
});

// Get system alerts
router.get('/alerts', (req, res) => {
  try {
    const alerts = systemMonitor.getSystemAlerts();
    
    res.json({
      success: true,
      data: alerts
    });
  } catch (error) {
    res.status(500).json({
      success: false,
      error: 'Failed to get system alerts',
      details: (error as Error).message
    });
  }
});

// Resolve alert
router.post('/alerts/:alertId/resolve', (req, res) => {
  try {
    const { alertId } = req.params;
    const resolved = systemMonitor.resolveAlert(alertId);
    
    res.json({
      success: resolved,
      message: resolved ? 'Alert resolved successfully' : 'Alert not found or already resolved'
    });
  } catch (error) {
    res.status(500).json({
      success: false,
      error: 'Failed to resolve alert',
      details: (error as Error).message
    });
  }
});

// Get component health
router.get('/health/:component?', (req, res) => {
  try {
    const { component } = req.params;
    const health = systemMonitor.getComponentHealth(component);
    
    res.json({
      success: true,
      data: health
    });
  } catch (error) {
    res.status(500).json({
      success: false,
      error: 'Failed to get component health',
      details: (error as Error).message
    });
  }
});

// Get connection status
router.get('/connections/:connectionId?', (req, res) => {
  try {
    const { connectionId } = req.params;
    
    if (connectionId) {
      const status = connectionManager.getConnectionStatus(connectionId);
      if (!status) {
        return res.status(404).json({
          success: false,
          error: 'Connection not found'
        });
      }
      
      res.json({
        success: true,
        data: status
      });
    } else {
      const connections = connectionManager.getAllConnections();
      res.json({
        success: true,
        data: connections
      });
    }
  } catch (error) {
    res.status(500).json({
      success: false,
      error: 'Failed to get connection status',
      details: (error as Error).message
    });
  }
});

// Test connection
router.post('/connections/:connectionId/test', async (req, res) => {
  try {
    const { connectionId } = req.params;
    const status = connectionManager.getConnectionStatus(connectionId);
    
    if (!status) {
      return res.status(404).json({
        success: false,
        error: 'Connection not found'
      });
    }
    
    res.json({
      success: true,
      data: {
        connectionId,
        status: status.status,
        lastHeartbeat: status.lastHeartbeat,
        metrics: status.metrics
      }
    });
  } catch (error) {
    res.status(500).json({
      success: false,
      error: 'Failed to test connection',
      details: (error as Error).message
    });
  }
});

export default router;