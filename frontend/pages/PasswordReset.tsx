import React, { useState } from 'react';
import { KeyRound } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Card, CardContent, CardHeader } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { useNavigate } from '@/lib/router';

export function PasswordResetPage() {
  const [token, setToken] = useState('');
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const navigate = useNavigate();

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    if (password !== confirmation) {
      setError('Passwords do not match.');
      return;
    }

    setSubmitting(true);
    try {
      const response = await fetch('/api/auth/password-reset/complete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: token.trim(), new_password: password }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail || 'Password reset failed.');
      }

      setToken('');
      setPassword('');
      setConfirmation('');
      navigate('/login', { replace: true });
    } catch (resetError) {
      setError(resetError instanceof Error ? resetError.message : 'Password reset failed.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-600 via-blue-500 to-purple-600 flex items-center justify-center p-4">
      <Card className="w-full max-w-md z-10 shadow-2xl">
        <CardHeader>
          <div className="text-center">
            <div className="w-12 h-12 bg-blue-600 rounded-xl flex items-center justify-center mx-auto mb-4">
              <KeyRound className="w-6 h-6 text-white" />
            </div>
            <h1 className="text-2xl font-bold text-gray-900">Choose a New Password</h1>
            <p className="text-gray-600 text-sm mt-2">
              Paste the short-lived token sent to you by an administrator.
            </p>
          </div>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            {error && <p className="text-sm text-red-700" role="alert">{error}</p>}
            <div>
              <label className="block text-sm font-medium mb-1">One-Time Token</label>
              <Input
                value={token}
                onChange={(event) => setToken(event.target.value)}
                minLength={32}
                maxLength={256}
                autoComplete="off"
                required
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">New Password</label>
              <Input
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                minLength={12}
                maxLength={1024}
                autoComplete="new-password"
                required
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Confirm New Password</label>
              <Input
                type="password"
                value={confirmation}
                onChange={(event) => setConfirmation(event.target.value)}
                minLength={12}
                maxLength={1024}
                autoComplete="new-password"
                required
              />
            </div>
            <Button type="submit" fullWidth loading={submitting}>Set Password</Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
