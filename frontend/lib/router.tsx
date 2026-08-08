import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';

const ALLOWED_PATHS = new Set([
  '/login',
  '/reset-password',
  '/dashboard',
  '/market',
  '/trading',
  '/agents',
  '/companions',
  '/reasoning',
  '/system',
  '/users',
  '/settings',
]);

function safeInternalPath(candidate: string): string {
  try {
    const parsed = new URL(candidate, window.location.origin);
    if (parsed.origin !== window.location.origin || !ALLOWED_PATHS.has(parsed.pathname)) {
      return '/dashboard';
    }
    return parsed.pathname;
  } catch {
    return '/dashboard';
  }
}

interface RouterContextValue {
  pathname: string;
  navigate: (path: string, options?: { replace?: boolean }) => void;
}

const RouterContext = createContext<RouterContextValue | null>(null);

export function RouterProvider({ children }: { children: React.ReactNode }) {
  const [pathname, setPathname] = useState(() => safeInternalPath(window.location.pathname));

  const navigate = useCallback((path: string, options?: { replace?: boolean }) => {
    const destination = safeInternalPath(path);
    if (options?.replace) {
      window.history.replaceState(null, '', destination);
    } else {
      window.history.pushState(null, '', destination);
    }
    setPathname(destination);
  }, []);

  useEffect(() => {
    const onPopState = () => setPathname(safeInternalPath(window.location.pathname));
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  const value = useMemo(() => ({ pathname, navigate }), [pathname, navigate]);
  return <RouterContext.Provider value={value}>{children}</RouterContext.Provider>;
}

function useRouter(): RouterContextValue {
  const context = useContext(RouterContext);
  if (!context) {
    throw new Error('Router hooks must be used inside RouterProvider');
  }
  return context;
}

export function useNavigate() {
  return useRouter().navigate;
}

export function useLocation() {
  const { pathname } = useRouter();
  return { pathname };
}

export function Navigate({ to, replace = false }: { to: string; replace?: boolean }) {
  const navigate = useNavigate();
  useEffect(() => navigate(to, { replace }), [navigate, replace, to]);
  return null;
}

interface LinkProps extends React.AnchorHTMLAttributes<HTMLAnchorElement> {
  to: string;
}

export function Link({ to, onClick, ...props }: LinkProps) {
  const navigate = useNavigate();
  const destination = safeInternalPath(to);
  return (
    <a
      {...props}
      href={destination}
      onClick={(event) => {
        onClick?.(event);
        if (
          event.defaultPrevented ||
          event.button !== 0 ||
          event.metaKey ||
          event.ctrlKey ||
          event.shiftKey ||
          event.altKey
        ) {
          return;
        }
        event.preventDefault();
        navigate(destination);
      }}
    />
  );
}
