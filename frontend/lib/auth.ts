import { useAuthStore } from '@/stores/authStore';

export function getAuthToken(): string | null {
  return useAuthStore.getState().token;
}

export function authHeaders(): Record<string, string> {
  const token = getAuthToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}
