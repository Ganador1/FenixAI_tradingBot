import { useState, useCallback, useRef, useEffect } from 'react';
import { API_CONFIG, HTTP_STATUS, ERROR_MESSAGES } from '@/lib/api-config';
import { toast } from 'sonner';

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH';
  headers?: Record<string, string>;
  body?: any;
  showNotification?: boolean;
  token?: string;
}

interface UseApiState<T> {
  data: T | null;
  loading: boolean;
  error: Error | null;
}

/**
 * Custom hook for making API requests with error handling and retry logic
 */
export const useApi = <T = any,>(
  url: string,
  options: RequestOptions = {}
): UseApiState<T> & { refetch: () => Promise<void> } => {
  const [state, setState] = useState<UseApiState<T>>({
    data: null,
    loading: false,
    error: null,
  });

  const attemptsRef = useRef(0);
  const abortControllerRef = useRef<AbortController | null>(null);

  const fetchData = useCallback(async (): Promise<void> => {
    // Cancel previous request
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }

    abortControllerRef.current = new AbortController();
    setState((prev) => ({ ...prev, loading: true, error: null }));

    try {
      const fullUrl = url.startsWith('http')
        ? url
        : `${API_CONFIG.baseURL}${url}`;

      const headers: Record<string, string> = {
        ...API_CONFIG.defaultHeaders,
        ...options.headers,
      };

      // Add authorization token if available
      const token = options.token || localStorage.getItem('auth_token');
      if (token) {
        headers['Authorization'] = `Bearer ${token}`;
      }

      // Set up timeout
      const timeoutId = setTimeout(() => {
        abortControllerRef.current?.abort();
      }, API_CONFIG.timeout);

      const response = await fetch(fullUrl, {
        method: options.method || 'GET',
        headers,
        body: options.body ? JSON.stringify(options.body) : undefined,
        signal: abortControllerRef.current.signal,
      });

      clearTimeout(timeoutId);

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        const errorMessage =
          errorData.error ||
          ERROR_MESSAGES[`HTTP_${response.status}` as keyof typeof ERROR_MESSAGES] ||
          ERROR_MESSAGES.UNKNOWN_ERROR;

        throw new Error(errorMessage);
      }

      const data = await response.json();
      setState({ data, loading: false, error: null });
      attemptsRef.current = 0;
    } catch (error: any) {
      // Ignore abort errors
      if (error.name === 'AbortError') {
        return;
      }

      // Retry logic for network errors and server errors
      const shouldRetry =
        attemptsRef.current < API_CONFIG.retries &&
        (error.message === ERROR_MESSAGES.NETWORK_ERROR ||
          error.message === ERROR_MESSAGES.TIMEOUT_ERROR);

      if (shouldRetry) {
        attemptsRef.current += 1;
        setTimeout(
          () => fetchData(),
          API_CONFIG.retryDelay * Math.pow(2, attemptsRef.current - 1)
        );
        return;
      }

      const appError = error instanceof Error ? error : new Error(ERROR_MESSAGES.UNKNOWN_ERROR);
      setState({ data: null, loading: false, error: appError });

      if (options.showNotification) {
        toast.error(appError.message);
      }
    }
  }, [url, options]);

  useEffect(() => {
    if (options.method === 'GET' || !options.method) {
      fetchData();
    }

    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
  }, [url, fetchData, options.method]);

  const refetch = useCallback(async (): Promise<void> => {
    attemptsRef.current = 0;
    await fetchData();
  }, [fetchData]);

  return {
    ...state,
    refetch,
  };
};

/**
 * Mutation hook for POST, PUT, DELETE requests
 */
export const useApiMutation = <T = any,>() => {
  const [state, setState] = useState<UseApiState<T> & { isLoading: boolean }>({
    data: null,
    loading: false,
    isLoading: false,
    error: null,
  });

  const abortControllerRef = useRef<AbortController | null>(null);

  const mutate = useCallback(
    async (
      url: string,
      options: RequestOptions = {}
    ): Promise<T | null> => {
      // Cancel previous request
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }

      abortControllerRef.current = new AbortController();
      setState((prev) => ({ ...prev, loading: true, isLoading: true, error: null }));

      try {
        const fullUrl = url.startsWith('http')
          ? url
          : `${API_CONFIG.baseURL}${url}`;

        const headers: Record<string, string> = {
          ...API_CONFIG.defaultHeaders,
          ...options.headers,
        };

        // Add authorization token if available
        const token = options.token || localStorage.getItem('auth_token');
        if (token) {
          headers['Authorization'] = `Bearer ${token}`;
        }

        // Set up timeout
        const timeoutId = setTimeout(() => {
          abortControllerRef.current?.abort();
        }, API_CONFIG.timeout);

        const response = await fetch(fullUrl, {
          method: options.method || 'POST',
          headers,
          body: options.body ? JSON.stringify(options.body) : undefined,
          signal: abortControllerRef.current.signal,
        });

        clearTimeout(timeoutId);

        if (!response.ok) {
          const errorData = await response.json().catch(() => ({}));
          const errorMessage =
            errorData.error ||
            ERROR_MESSAGES[`HTTP_${response.status}` as keyof typeof ERROR_MESSAGES] ||
            ERROR_MESSAGES.UNKNOWN_ERROR;

          throw new Error(errorMessage);
        }

        const data = await response.json();
        setState({ data, loading: false, isLoading: false, error: null });

        if (options.showNotification) {
          toast.success('Operation successful!');
        }

        return data;
      } catch (error: any) {
        // Ignore abort errors
        if (error.name === 'AbortError') {
          return null;
        }

        const appError = error instanceof Error ? error : new Error(ERROR_MESSAGES.UNKNOWN_ERROR);
        setState({ data: null, loading: false, isLoading: false, error: appError });

        if (options.showNotification) {
          toast.error(appError.message);
        }

        throw appError;
      }
    },
    []
  );

  const reset = useCallback(() => {
    setState({
      data: null,
      loading: false,
      isLoading: false,
      error: null,
    });
  }, []);

  return {
    ...state,
    mutate,
    reset,
  };
};
