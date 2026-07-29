import React, { useEffect } from "react";
import { Toaster } from "sonner";

// Components
import { Layout } from "@/components/Layout";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { ProtectedRoute } from "@/components/ProtectedRoute";

// Pages
import { ModernLoginPage } from "@/components/ModernLoginPage";
import { Dashboard } from "@/pages/Dashboard";
import { MarketData } from "@/pages/MarketData";
import { Trading } from "@/pages/Trading";
import { Agents } from "@/pages/Agents";
import { Companions } from "@/pages/Companions";
import { ReasoningBank } from "@/pages/ReasoningBank";
import { SystemMonitor } from "@/pages/SystemMonitor";
import { UsersPage } from "@/pages/Users";
import { SettingsPage } from "@/pages/Settings";
import { Navigate, RouterProvider, useLocation } from "@/lib/router";

// Store
import { useAuthStore } from "@/stores/authStore";
import { useSystemStore } from "@/stores/systemStore";
import { useAgentStore } from "@/stores/agentStore";

const PROTECTED_PAGES: Record<string, JSX.Element> = {
  '/dashboard': <Dashboard />,
  '/market': <MarketData />,
  '/trading': <Trading />,
  '/agents': <Agents />,
  '/companions': <Companions />,
  '/reasoning': <ReasoningBank />,
  '/system': <SystemMonitor />,
  '/users': <UsersPage />,
  '/settings': <SettingsPage />,
};

function AppRoutes(): JSX.Element {
  const { user } = useAuthStore();
  const { pathname } = useLocation();
  const { initializeSocket: initializeSystemSocket, disconnectSocket: disconnectSystemSocket } = useSystemStore();
  const { initializeSocket: initializeAgentSocket, disconnectSocket: disconnectAgentSocket } = useAgentStore();

  useEffect(() => {
    if (user) {
      // Initialize WebSocket connections
      initializeSystemSocket();
      initializeAgentSocket();
    }

    return () => {
      // Cleanup sockets on unmount
      disconnectSystemSocket();
      disconnectAgentSocket();
    };
  }, [user, initializeSystemSocket, initializeAgentSocket, disconnectSystemSocket, disconnectAgentSocket]);

  if (pathname === '/login') {
    return user ? <Navigate to="/dashboard" replace /> : <ModernLoginPage />;
  }
  const page = PROTECTED_PAGES[pathname];
  if (!page) {
    return <Navigate to="/dashboard" replace />;
  }
  return (
    <ProtectedRoute>
      <Layout>{page}</Layout>
    </ProtectedRoute>
  );
}

function App(): JSX.Element {
  return (
    <ErrorBoundary>
      <RouterProvider>
        <AppRoutes />
      </RouterProvider>
      <Toaster position="top-right" richColors />
    </ErrorBoundary>
  );
}

export default App;
