import React, { useState, useEffect } from 'react';
import { Save, RefreshCw, AlertTriangle, CheckCircle } from 'lucide-react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { Switch } from '@/components/ui/Switch';
import { Alert, AlertTitle, AlertDescription } from '@/components/ui/Alert';
// import { Badge } from '@/components/ui/Badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/Tabs';

interface SystemSettings {
  general: {
    site_name: string;
    site_description: string;
    timezone: string;
    date_format: string;
    language: string;
  };
  security: {
    session_timeout: number;
    password_min_length: number;
    require_uppercase: boolean;
    require_lowercase: boolean;
    require_numbers: boolean;
    require_special_chars: boolean;
    max_login_attempts: number;
    lockout_duration: number;
    two_factor_enabled: boolean;
  };
  notifications: {
    email_enabled: boolean;
    email_host: string;
    email_port: number;
    email_username: string;
    email_password: string;
    email_from: string;
    sms_enabled: boolean;
    sms_provider: string;
    sms_api_key: string;
  };
  trading: {
    max_positions_per_user: number;
    max_daily_trades: number;
    risk_threshold: number;
    stop_loss_default: number;
    take_profit_default: number;
    leverage_max: number;
    margin_call_level: number;
    auto_close_on_margin_call: boolean;
  };
  agents: {
    sentiment_agent_enabled: boolean;
    technical_agent_enabled: boolean;
    visual_agent_enabled: boolean;
    qabba_agent_enabled: boolean;
    decision_agent_enabled: boolean;
    risk_agent_enabled: boolean;
    agent_timeout: number;
    max_concurrent_agents: number;
    reasoning_bank_retention_days: number;
    scorecard_retention_days: number;
  };
  api: {
    rate_limit_enabled: boolean;
    rate_limit_requests_per_minute: number;
    rate_limit_requests_per_hour: number;
    cors_enabled: boolean;
    cors_origins: string[];
    api_key_required: boolean;
    jwt_expiry_hours: number;
    refresh_token_expiry_days: number;
  };
  database: {
    backup_enabled: boolean;
    backup_frequency: string;
    backup_retention_days: number;
    maintenance_window: string;
    auto_vacuum: boolean;
    connection_pool_size: number;
    query_timeout_seconds: number;
  };
}

export const SettingsPage: React.FC = () => {
  const [settings, setSettings] = useState<SystemSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [activeTab] = useState('general');
  const [testResults, setTestResults] = useState<Record<string, boolean>>({});

  useEffect(() => {
    fetchSettings();
  }, []);

  const fetchSettings = async () => {
    try {
      setLoading(true);
      setError(null);

      const response = await fetch('/api/system/settings', {
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('token')}`
        }
      });

      if (!response.ok) {
        throw new Error('Failed to fetch settings');
      }

      const data = await response.json();
      setSettings(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch settings');
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async (section: keyof SystemSettings) => {
    if (!settings) return;

    try {
      setSaving(true);
      setError(null);
      setSuccess(null);

      const response = await fetch(`/api/system/settings/${section}`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${localStorage.getItem('token')}`
        },
        body: JSON.stringify(settings[section])
      });

      if (!response.ok) {
        throw new Error('Failed to save settings');
      }

      setSuccess(`${section.charAt(0).toUpperCase() + section.slice(1)} settings saved successfully`);
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save settings');
    } finally {
      setSaving(false);
    }
  };

  const handleTestConnection = async (type: 'email' | 'sms' | 'database') => {
    try {
      setTestResults(prev => ({ ...prev, [type]: false }));
      
      const response = await fetch(`/api/system/test-connection/${type}`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('token')}`
        }
      });

      const result = await response.json();
      setTestResults(prev => ({ ...prev, [type]: result.success }));
      
      setTimeout(() => {
        setTestResults(prev => ({ ...prev, [type]: undefined as unknown as boolean }));
      }, 3000);
    } catch {
      setTestResults(prev => ({ ...prev, [type]: false }));
      setTimeout(() => {
        setTestResults(prev => ({ ...prev, [type]: undefined as unknown as boolean }));
      }, 3000);
    }
  };

  const handleResetToDefaults = async (section: keyof SystemSettings) => {
    if (!confirm(`Are you sure you want to reset ${section} settings to defaults?`)) return;

    try {
      setSaving(true);
      setError(null);

      const response = await fetch(`/api/system/settings/${section}/reset`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('token')}`
        }
      });

      if (!response.ok) {
        throw new Error('Failed to reset settings');
      }

      await fetchSettings();
      setSuccess(`${section.charAt(0).toUpperCase() + section.slice(1)} settings reset to defaults`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to reset settings');
    } finally {
      setSaving(false);
    }
  };

  const updateSetting = (section: keyof SystemSettings, key: string, value: unknown) => {
    if (!settings) return;
    
    setSettings(prev => ({
      ...prev!,
      [section]: {
        ...prev![section],
        [key]: value
      }
    }));
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
      </div>
    );
  }

  if (!settings) {
    return (
      <Alert variant="error">
        <AlertTriangle className="h-4 w-4" />
        <AlertTitle>Error</AlertTitle>
        <AlertDescription>Failed to load settings</AlertDescription>
      </Alert>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Settings</h1>
        <Button
          variant="outline"
          size="sm"
          onClick={fetchSettings}
          className="flex items-center space-x-1"
        >
          <RefreshCw className="h-4 w-4" />
          <span>Refresh</span>
        </Button>
      </div>

      {/* Alerts */}
      {error && (
        <Alert variant="error" className="mb-4">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>Error</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {success && (
        <Alert variant="success" className="mb-4">
          <CheckCircle className="h-4 w-4" />
          <AlertTitle>Success</AlertTitle>
          <AlertDescription>{success}</AlertDescription>
        </Alert>
      )}

      {/* Settings Tabs */}
      <Tabs defaultValue={activeTab}>
        <TabsList className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-2">
          <TabsTrigger value="general">General</TabsTrigger>
          <TabsTrigger value="security">Security</TabsTrigger>
          <TabsTrigger value="notifications">Notifications</TabsTrigger>
          <TabsTrigger value="trading">Trading</TabsTrigger>
          <TabsTrigger value="agents">Agents</TabsTrigger>
          <TabsTrigger value="api">API</TabsTrigger>
          <TabsTrigger value="database">Database</TabsTrigger>
        </TabsList>

        {/* General Settings */}
        <TabsContent value="general" className="space-y-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle>General Settings</CardTitle>
              <div className="flex space-x-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => handleResetToDefaults('general')}
                >
                  Reset to Defaults
                </Button>
                <Button
                  size="sm"
                  onClick={() => handleSave('general')}
                  disabled={saving}
                >
                  <Save className="h-4 w-4 mr-1" />
                  Save Changes
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <label className="block text-sm font-medium mb-1">Site Name</label>
                <Input
                  value={settings.general.site_name}
                  onChange={(e) => updateSetting('general', 'site_name', e.target.value)}
                  placeholder="Trading Dashboard"
                />
              </div>
              
              <div>
                <label className="block text-sm font-medium mb-1">Site Description</label>
                <Input
                  value={settings.general.site_description}
                  onChange={(e) => updateSetting('general', 'site_description', e.target.value)}
                  placeholder="Advanced trading dashboard with AI agents"
                />
              </div>
              
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium mb-1">Timezone</label>
                  <Select
                    value={settings.general.timezone}
                    onChange={(e) => updateSetting('general', 'timezone', e.target.value)}
                  >
                    <option value="UTC">UTC</option>
                    <option value="America/New_York">Eastern Time</option>
                    <option value="America/Chicago">Central Time</option>
                    <option value="America/Denver">Mountain Time</option>
                    <option value="America/Los_Angeles">Pacific Time</option>
                    <option value="Europe/London">London</option>
                    <option value="Europe/Paris">Paris</option>
                    <option value="Asia/Tokyo">Tokyo</option>
                  </Select>
                </div>
                
                <div>
                  <label className="block text-sm font-medium mb-1">Date Format</label>
                  <Select
                    value={settings.general.date_format}
                    onChange={(e) => updateSetting('general', 'date_format', e.target.value)}
                  >
                    <option value="MM/DD/YYYY">MM/DD/YYYY</option>
                    <option value="DD/MM/YYYY">DD/MM/YYYY</option>
                    <option value="YYYY-MM-DD">YYYY-MM-DD</option>
                  </Select>
                </div>
              </div>
              
              <div>
                <label className="block text-sm font-medium mb-1">Language</label>
                <Select
                  value={settings.general.language}
                  onChange={(e) => updateSetting('general', 'language', e.target.value)}
                >
                  <option value="en">English</option>
                  <option value="es">Spanish</option>
                  <option value="fr">French</option>
                  <option value="de">German</option>
                  <option value="zh">Chinese</option>
                  <option value="ja">Japanese</option>
                </Select>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* Security Settings */}
        <TabsContent value="security" className="space-y-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle>Security Settings</CardTitle>
              <div className="flex space-x-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => handleResetToDefaults('security')}
                >
                  Reset to Defaults
                </Button>
                <Button
                  size="sm"
                  onClick={() => handleSave('security')}
                  disabled={saving}
                >
                  <Save className="h-4 w-4 mr-1" />
                  Save Changes
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium mb-1">Session Timeout (minutes)</label>
                  <Input
                    type="number"
                    value={settings.security.session_timeout}
                    onChange={(e) => updateSetting('security', 'session_timeout', parseInt(e.target.value))}
                    min="5"
                    max="1440"
                  />
                </div>
                
                <div>
                  <label className="block text-sm font-medium mb-1">Password Min Length</label>
                  <Input
                    type="number"
                    value={settings.security.password_min_length}
                    onChange={(e) => updateSetting('security', 'password_min_length', parseInt(e.target.value))}
                    min="6"
                    max="32"
                  />
                </div>
              </div>
              
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium mb-1">Max Login Attempts</label>
                  <Input
                    type="number"
                    value={settings.security.max_login_attempts}
                    onChange={(e) => updateSetting('security', 'max_login_attempts', parseInt(e.target.value))}
                    min="3"
                    max="10"
                  />
                </div>
                
                <div>
                  <label className="block text-sm font-medium mb-1">Lockout Duration (minutes)</label>
                  <Input
                    type="number"
                    value={settings.security.lockout_duration}
                    onChange={(e) => updateSetting('security', 'lockout_duration', parseInt(e.target.value))}
                    min="5"
                    max="1440"
                  />
                </div>
              </div>
              
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium">Require Uppercase Letters</label>
                  <Switch
                    checked={settings.security.require_uppercase}
                    onChange={(checked) => updateSetting('security', 'require_uppercase', checked)}
                  />
                </div>
                
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium">Require Lowercase Letters</label>
                  <Switch
                    checked={settings.security.require_lowercase}
                    onChange={(checked) => updateSetting('security', 'require_lowercase', checked)}
                  />
                </div>
                
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium">Require Numbers</label>
                  <Switch
                    checked={settings.security.require_numbers}
                    onChange={(checked) => updateSetting('security', 'require_numbers', checked)}
                  />
                </div>
                
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium">Require Special Characters</label>
                  <Switch
                    checked={settings.security.require_special_chars}
                    onChange={(checked) => updateSetting('security', 'require_special_chars', checked)}
                  />
                </div>
                
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium">Two-Factor Authentication</label>
                  <Switch
                    checked={settings.security.two_factor_enabled}
                    onChange={(checked) => updateSetting('security', 'two_factor_enabled', checked)}
                  />
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* Notification Settings */}
        <TabsContent value="notifications" className="space-y-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle>Notification Settings</CardTitle>
              <div className="flex space-x-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => handleResetToDefaults('notifications')}
                >
                  Reset to Defaults
                </Button>
                <Button
                  size="sm"
                  onClick={() => handleSave('notifications')}
                  disabled={saving}
                >
                  <Save className="h-4 w-4 mr-1" />
                  Save Changes
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Email Settings */}
              <div className="border rounded-lg p-4">
                <div className="flex items-center justify-between mb-4">
                  <h4 className="font-medium">Email Settings</h4>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => handleTestConnection('email')}
                    disabled={testResults.email !== undefined}
                  >
                    {testResults.email === undefined && 'Test Connection'}
                    {testResults.email === true && <CheckCircle className="h-4 w-4 text-green-500" />}
                    {testResults.email === false && <AlertTriangle className="h-4 w-4 text-red-500" />}
                  </Button>
                </div>
                
                <div className="flex items-center justify-between mb-4">
                  <label className="text-sm font-medium">Email Notifications</label>
                  <Switch
                    checked={settings.notifications.email_enabled}
                    onChange={(checked) => updateSetting('notifications', 'email_enabled', checked)}
                  />
                </div>
                
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium mb-1">SMTP Host</label>
                    <Input
                      value={settings.notifications.email_host}
                      onChange={(e) => updateSetting('notifications', 'email_host', e.target.value)}
                      placeholder="smtp.gmail.com"
                      disabled={!settings.notifications.email_enabled}
                    />
                  </div>
                  
                  <div>
                    <label className="block text-sm font-medium mb-1">SMTP Port</label>
                    <Input
                      type="number"
                      value={settings.notifications.email_port}
                      onChange={(e) => updateSetting('notifications', 'email_port', parseInt(e.target.value))}
                      placeholder="587"
                      disabled={!settings.notifications.email_enabled}
                    />
                  </div>
                </div>
                
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium mb-1">Username</label>
                    <Input
                      value={settings.notifications.email_username}
                      onChange={(e) => updateSetting('notifications', 'email_username', e.target.value)}
                      placeholder="your-email@gmail.com"
                      disabled={!settings.notifications.email_enabled}
                    />
                  </div>
                  
                  <div>
                    <label className="block text-sm font-medium mb-1">Password</label>
                    <Input
                      type="password"
                      value={settings.notifications.email_password}
                      onChange={(e) => updateSetting('notifications', 'email_password', e.target.value)}
                      placeholder="Your email password"
                      disabled={!settings.notifications.email_enabled}
                    />
                  </div>
                </div>
                
                <div>
                  <label className="block text-sm font-medium mb-1">From Address</label>
                  <Input
                    value={settings.notifications.email_from}
                    onChange={(e) => updateSetting('notifications', 'email_from', e.target.value)}
                    placeholder="noreply@yourdomain.com"
                    disabled={!settings.notifications.email_enabled}
                  />
                </div>
              </div>
              
              {/* SMS Settings */}
              <div className="border rounded-lg p-4">
                <div className="flex items-center justify-between mb-4">
                  <h4 className="font-medium">SMS Settings</h4>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => handleTestConnection('sms')}
                    disabled={testResults.sms !== undefined}
                  >
                    {testResults.sms === undefined && 'Test Connection'}
                    {testResults.sms === true && <CheckCircle className="h-4 w-4 text-green-500" />}
                    {testResults.sms === false && <AlertTriangle className="h-4 w-4 text-red-500" />}
                  </Button>
                </div>
                
                <div className="flex items-center justify-between mb-4">
                  <label className="text-sm font-medium">SMS Notifications</label>
                  <Switch
                    checked={settings.notifications.sms_enabled}
                    onChange={(checked) => updateSetting('notifications', 'sms_enabled', checked)}
                  />
                </div>
                
                <div>
                  <label className="block text-sm font-medium mb-1">SMS Provider</label>
                  <Select
                    value={settings.notifications.sms_provider}
                    onChange={(e) => updateSetting('notifications', 'sms_provider', e.target.value)}
                    disabled={!settings.notifications.sms_enabled}
                  >
                    <option value="twilio">Twilio</option>
                    <option value="nexmo">Nexmo</option>
                    <option value="plivo">Plivo</option>
                    <option value="messagebird">MessageBird</option>
                  </Select>
                </div>
                
                <div>
                  <label className="block text-sm font-medium mb-1">API Key</label>
                  <Input
                    value={settings.notifications.sms_api_key}
                    onChange={(e) => updateSetting('notifications', 'sms_api_key', e.target.value)}
                    placeholder="Your SMS API key"
                    disabled={!settings.notifications.sms_enabled}
                  />
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* Trading Settings */}
        <TabsContent value="trading" className="space-y-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle>Trading Settings</CardTitle>
              <div className="flex space-x-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => handleResetToDefaults('trading')}
                >
                  Reset to Defaults
                </Button>
                <Button
                  size="sm"
                  onClick={() => handleSave('trading')}
                  disabled={saving}
                >
                  <Save className="h-4 w-4 mr-1" />
                  Save Changes
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium mb-1">Max Positions Per User</label>
                  <Input
                    type="number"
                    value={settings.trading.max_positions_per_user}
                    onChange={(e) => updateSetting('trading', 'max_positions_per_user', parseInt(e.target.value))}
                    min="1"
                    max="100"
                  />
                </div>
                
                <div>
                  <label className="block text-sm font-medium mb-1">Max Daily Trades</label>
                  <Input
                    type="number"
                    value={settings.trading.max_daily_trades}
                    onChange={(e) => updateSetting('trading', 'max_daily_trades', parseInt(e.target.value))}
                    min="1"
                    max="1000"
                  />
                </div>
              </div>
              
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium mb-1">Risk Threshold (%)</label>
                  <Input
                    type="number"
                    step="0.1"
                    value={settings.trading.risk_threshold}
                    onChange={(e) => updateSetting('trading', 'risk_threshold', parseFloat(e.target.value))}
                    min="0.1"
                    max="100"
                  />
                </div>
                
                <div>
                  <label className="block text-sm font-medium mb-1">Max Leverage</label>
                  <Input
                    type="number"
                    step="0.1"
                    value={settings.trading.leverage_max}
                    onChange={(e) => updateSetting('trading', 'leverage_max', parseFloat(e.target.value))}
                    min="1"
                    max="100"
                  />
                </div>
              </div>
              
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium mb-1">Default Stop Loss (%)</label>
                  <Input
                    type="number"
                    step="0.1"
                    value={settings.trading.stop_loss_default}
                    onChange={(e) => updateSetting('trading', 'stop_loss_default', parseFloat(e.target.value))}
                    min="0.1"
                    max="50"
                  />
                </div>
                
                <div>
                  <label className="block text-sm font-medium mb-1">Default Take Profit (%)</label>
                  <Input
                    type="number"
                    step="0.1"
                    value={settings.trading.take_profit_default}
                    onChange={(e) => updateSetting('trading', 'take_profit_default', parseFloat(e.target.value))}
                    min="0.1"
                    max="100"
                  />
                </div>
              </div>
              
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium mb-1">Margin Call Level (%)</label>
                  <Input
                    type="number"
                    step="0.1"
                    value={settings.trading.margin_call_level}
                    onChange={(e) => updateSetting('trading', 'margin_call_level', parseFloat(e.target.value))}
                    min="10"
                    max="100"
                  />
                </div>
                
                <div className="flex items-end">
                  <div className="flex items-center justify-between w-full">
                    <label className="text-sm font-medium">Auto-close on Margin Call</label>
                    <Switch
                      checked={settings.trading.auto_close_on_margin_call}
                      onChange={(checked) => updateSetting('trading', 'auto_close_on_margin_call', checked)}
                    />
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* Agent Settings */}
        <TabsContent value="agents" className="space-y-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle>AI Agent Settings</CardTitle>
              <div className="flex space-x-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => handleResetToDefaults('agents')}
                >
                  Reset to Defaults
                </Button>
                <Button
                  size="sm"
                  onClick={() => handleSave('agents')}
                  disabled={saving}
                >
                  <Save className="h-4 w-4 mr-1" />
                  Save Changes
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 md:grid-cols-3 gap-4 p-4 border rounded-lg">
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium">Sentiment Agent</label>
                  <Switch
                    checked={settings.agents.sentiment_agent_enabled}
                    onChange={(checked) => updateSetting('agents', 'sentiment_agent_enabled', checked)}
                  />
                </div>
                
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium">Technical Agent</label>
                  <Switch
                    checked={settings.agents.technical_agent_enabled}
                    onChange={(checked) => updateSetting('agents', 'technical_agent_enabled', checked)}
                  />
                </div>
                
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium">Visual Agent</label>
                  <Switch
                    checked={settings.agents.visual_agent_enabled}
                    onChange={(checked) => updateSetting('agents', 'visual_agent_enabled', checked)}
                  />
                </div>
                
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium">Qabba Agent</label>
                  <Switch
                    checked={settings.agents.qabba_agent_enabled}
                    onChange={(checked) => updateSetting('agents', 'qabba_agent_enabled', checked)}
                  />
                </div>
                
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium">Decision Agent</label>
                  <Switch
                    checked={settings.agents.decision_agent_enabled}
                    onChange={(checked) => updateSetting('agents', 'decision_agent_enabled', checked)}
                  />
                </div>
                
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium">Risk Agent</label>
                  <Switch
                    checked={settings.agents.risk_agent_enabled}
                    onChange={(checked) => updateSetting('agents', 'risk_agent_enabled', checked)}
                  />
                </div>
              </div>
              
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium mb-1">Agent Timeout (seconds)</label>
                  <Input
                    type="number"
                    value={settings.agents.agent_timeout}
                    onChange={(e) => updateSetting('agents', 'agent_timeout', parseInt(e.target.value))}
                    min="30"
                    max="300"
                  />
                </div>
                
                <div>
                  <label className="block text-sm font-medium mb-1">Max Concurrent Agents</label>
                  <Input
                    type="number"
                    value={settings.agents.max_concurrent_agents}
                    onChange={(e) => updateSetting('agents', 'max_concurrent_agents', parseInt(e.target.value))}
                    min="1"
                    max="10"
                  />
                </div>
              </div>
              
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium mb-1">Reasoning Bank Retention (days)</label>
                  <Input
                    type="number"
                    value={settings.agents.reasoning_bank_retention_days}
                    onChange={(e) => updateSetting('agents', 'reasoning_bank_retention_days', parseInt(e.target.value))}
                    min="1"
                    max="365"
                  />
                </div>
                
                <div>
                  <label className="block text-sm font-medium mb-1">Scorecard Retention (days)</label>
                  <Input
                    type="number"
                    value={settings.agents.scorecard_retention_days}
                    onChange={(e) => updateSetting('agents', 'scorecard_retention_days', parseInt(e.target.value))}
                    min="1"
                    max="365"
                  />
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* API Settings */}
        <TabsContent value="api" className="space-y-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle>API Settings</CardTitle>
              <div className="flex space-x-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => handleResetToDefaults('api')}
                >
                  Reset to Defaults
                </Button>
                <Button
                  size="sm"
                  onClick={() => handleSave('api')}
                  disabled={saving}
                >
                  <Save className="h-4 w-4 mr-1" />
                  Save Changes
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium">Rate Limiting</label>
                  <Switch
                    checked={settings.api.rate_limit_enabled}
                    onChange={(checked) => updateSetting('api', 'rate_limit_enabled', checked)}
                  />
                </div>
                
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium">CORS Enabled</label>
                  <Switch
                    checked={settings.api.cors_enabled}
                    onChange={(checked) => updateSetting('api', 'cors_enabled', checked)}
                  />
                </div>
                
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium">API Key Required</label>
                  <Switch
                    checked={settings.api.api_key_required}
                    onChange={(checked) => updateSetting('api', 'api_key_required', checked)}
                  />
                </div>
              </div>
              
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium mb-1">Requests Per Minute</label>
                  <Input
                    type="number"
                    value={settings.api.rate_limit_requests_per_minute}
                    onChange={(e) => updateSetting('api', 'rate_limit_requests_per_minute', parseInt(e.target.value))}
                    min="10"
                    max="1000"
                  />
                </div>
                
                <div>
                  <label className="block text-sm font-medium mb-1">Requests Per Hour</label>
                  <Input
                    type="number"
                    value={settings.api.rate_limit_requests_per_hour}
                    onChange={(e) => updateSetting('api', 'rate_limit_requests_per_hour', parseInt(e.target.value))}
                    min="100"
                    max="10000"
                  />
                </div>
              </div>
              
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium mb-1">JWT Expiry (hours)</label>
                  <Input
                    type="number"
                    value={settings.api.jwt_expiry_hours}
                    onChange={(e) => updateSetting('api', 'jwt_expiry_hours', parseInt(e.target.value))}
                    min="1"
                    max="168"
                  />
                </div>
                
                <div>
                  <label className="block text-sm font-medium mb-1">Refresh Token Expiry (days)</label>
                  <Input
                    type="number"
                    value={settings.api.refresh_token_expiry_days}
                    onChange={(e) => updateSetting('api', 'refresh_token_expiry_days', parseInt(e.target.value))}
                    min="1"
                    max="365"
                  />
                </div>
              </div>
              
              <div>
                <label className="block text-sm font-medium mb-1">CORS Origins</label>
                <Input
                  value={settings.api.cors_origins.join(', ')}
                  onChange={(e) => updateSetting('api', 'cors_origins', e.target.value.split(',').map(s => s.trim()))}
                  placeholder="http://localhost:3000, https://yourdomain.com"
                />
                <p className="text-xs text-gray-500 mt-1">Comma-separated list of allowed origins</p>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* Database Settings */}
        <TabsContent value="database" className="space-y-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle>Database Settings</CardTitle>
              <div className="flex space-x-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => handleTestConnection('database')}
                  disabled={testResults.database !== undefined}
                >
                  {testResults.database === undefined && 'Test Connection'}
                  {testResults.database === true && <CheckCircle className="h-4 w-4 text-green-500" />}
                  {testResults.database === false && <AlertTriangle className="h-4 w-4 text-red-500" />}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => handleResetToDefaults('database')}
                >
                  Reset to Defaults
                </Button>
                <Button
                  size="sm"
                  onClick={() => handleSave('database')}
                  disabled={saving}
                >
                  <Save className="h-4 w-4 mr-1" />
                  Save Changes
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium">Backup Enabled</label>
                  <Switch
                    checked={settings.database.backup_enabled}
                    onChange={(checked) => updateSetting('database', 'backup_enabled', checked)}
                  />
                </div>
                
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium">Auto Vacuum</label>
                  <Switch
                    checked={settings.database.auto_vacuum}
                    onChange={(checked) => updateSetting('database', 'auto_vacuum', checked)}
                  />
                </div>
              </div>
              
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium mb-1">Backup Frequency</label>
                  <Select
                    value={settings.database.backup_frequency}
                    onChange={(e) => updateSetting('database', 'backup_frequency', e.target.value)}
                    disabled={!settings.database.backup_enabled}
                  >
                    <option value="daily">Daily</option>
                    <option value="weekly">Weekly</option>
                    <option value="monthly">Monthly</option>
                  </Select>
                </div>
                
                <div>
                  <label className="block text-sm font-medium mb-1">Backup Retention (days)</label>
                  <Input
                    type="number"
                    value={settings.database.backup_retention_days}
                    onChange={(e) => updateSetting('database', 'backup_retention_days', parseInt(e.target.value))}
                    min="1"
                    max="365"
                    disabled={!settings.database.backup_enabled}
                  />
                </div>
              </div>
              
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium mb-1">Connection Pool Size</label>
                  <Input
                    type="number"
                    value={settings.database.connection_pool_size}
                    onChange={(e) => updateSetting('database', 'connection_pool_size', parseInt(e.target.value))}
                    min="5"
                    max="100"
                  />
                </div>
                
                <div>
                  <label className="block text-sm font-medium mb-1">Query Timeout (seconds)</label>
                  <Input
                    type="number"
                    value={settings.database.query_timeout_seconds}
                    onChange={(e) => updateSetting('database', 'query_timeout_seconds', parseInt(e.target.value))}
                    min="5"
                    max="300"
                  />
                </div>
              </div>
              
              <div>
                <label className="block text-sm font-medium mb-1">Maintenance Window</label>
                <Input
                  value={settings.database.maintenance_window}
                  onChange={(e) => updateSetting('database', 'maintenance_window', e.target.value)}
                  placeholder="02:00-04:00"
                />
                <p className="text-xs text-gray-500 mt-1">Time window for database maintenance (HH:MM-HH:MM)</p>
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
};