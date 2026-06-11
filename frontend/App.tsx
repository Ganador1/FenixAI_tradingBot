import React, { useEffect } from "react";
import {
  BrowserRouter as Router,
  Routes,
  Route,
  Navigate,
} from "react-router-dom";
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

// Store
import { useAuthStore } from "@/stores/authStore";
import { useSystemStore } from "@/stores/systemStore";
import { useAgentStore } from "@/stores/agentStore";

function App(): JSX.Element {
  const { user } = useAuthStore();
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

  return (
    <ErrorBoundary>
      <Router>
        <Routes>
          {/* Public Routes */}
          <Route path="/login" element={<ModernLoginPage />} />

          {/* Protected Routes */}
          <Route
            path="/"
            element={
              <ProtectedRoute>
                <Layout />
              </ProtectedRoute>
            }
          >
            <Route index element={<Navigate to="/dashboard" replace />} />
            <Route path="dashboard" element={<Dashboard />} />
            <Route path="market" element={<MarketData />} />
            <Route path="trading" element={<Trading />} />
            <Route path="agents" element={<Agents />} />
            <Route path="companions" element={<Companions />} />
            <Route path="reasoning" element={<ReasoningBank />} />
            <Route path="system" element={<SystemMonitor />} />
            <Route path="users" element={<UsersPage />} />
            <Route path="settings" element={<SettingsPage />} />
          </Route>

          {/* Catch-all Route */}
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </Router>

      {/* Global Toast Notifications */}
      <Toaster position="top-right" richColors />
    </ErrorBoundary>
  );
}

export default App;