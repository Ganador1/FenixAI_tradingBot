import React from 'react';
import { AlertTriangle, CheckCircle, Info, XCircle } from 'lucide-react';
import { SystemAlert } from '@/stores/systemStore';
import { formatDate } from '@/lib/utils';
import { Link } from '@/lib/router';

interface RecentAlertsProps {
  alerts: SystemAlert[];
}

export function RecentAlerts({ alerts }: RecentAlertsProps) {
  const getAlertIcon = (type: SystemAlert['type']) => {
    switch (type) {
      case 'error':
        return <XCircle className="w-4 h-4 text-red-500" />;
      case 'warning':
        return <AlertTriangle className="w-4 h-4 text-yellow-500" />;
      case 'info':
        return <Info className="w-4 h-4 text-blue-500" />;
      default:
        return <CheckCircle className="w-4 h-4 text-green-500" />;
    }
  };

  const getSeverityColor = (severity: SystemAlert['severity']) => {
    switch (severity) {
      case 'critical':
        return 'border-red-200 bg-red-50';
      case 'high':
        return 'border-orange-200 bg-orange-50';
      case 'medium':
        return 'border-yellow-200 bg-yellow-50';
      case 'low':
        return 'border-blue-200 bg-blue-50';
      default:
        return 'border-gray-200 bg-gray-50';
    }
  };

  if (alerts.length === 0) {
    return (
      <div className="text-center py-8 text-gray-500">
        <CheckCircle className="w-12 h-12 mx-auto mb-2 text-green-500" />
        <p>No recent alerts</p>
        <p className="text-sm">System is running smoothly</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {alerts.map((alert) => (
        <div
          key={alert.id}
          className={`p-3 rounded-lg border ${getSeverityColor(alert.severity)}`}
        >
          <div className="flex items-start space-x-3">
            {getAlertIcon(alert.type)}
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between">
                <p className="text-sm font-medium text-gray-900 truncate">
                  {alert.title}
                </p>
                <span className={`text-xs px-2 py-1 rounded-full ${
                  alert.resolved 
                    ? 'bg-green-100 text-green-800' 
                    : 'bg-yellow-100 text-yellow-800'
                }`}>
                  {alert.resolved ? 'Resolved' : 'Active'}
                </span>
              </div>
              <p className="text-sm text-gray-600 mt-1">
                {alert.message}
              </p>
              <div className="flex items-center justify-between mt-2">
                <span className="text-xs text-gray-500">
                  {alert.component}
                </span>
                <span className="text-xs text-gray-500">
                  {formatDate(alert.created_at)}
                </span>
              </div>
            </div>
          </div>
        </div>
      ))}
      
      {alerts.length > 0 && (
        <div className="text-center pt-2">
          <Link
            to="/system"
            className="text-sm text-blue-600 hover:text-blue-800 font-medium"
          >
            View all alerts
          </Link>
        </div>
      )}
    </div>
  );
}
