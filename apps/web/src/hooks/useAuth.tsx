/**
 * Authentication Context and Hook.
 * Strictly relies on backend /api/v1/auth/me contract.
 * No silent auto-login on failure. No fake token forging.
 */
'use client';

import React, { createContext, useContext, useEffect, useState, useCallback } from 'react';
import type { AuthenticatedPrincipal, UserRole } from '@/types/api';
import { apiClient, setAuthToken, getAuthToken, ApiError, getApiBaseUrl } from '@/services/api';
import { MOCK_PRINCIPAL } from '@/services/mock-data';

interface AuthContextType {
  principal: AuthenticatedPrincipal | null;
  role: UserRole | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  error: string | null;
  isDemoPreview: boolean;
  enableDemoPreview: (enabled: boolean) => void;
  setToken: (token: string | null) => Promise<void>;
  loginDevOfficer: (credentials?: { email?: string; password?: string; role?: UserRole | string }) => Promise<boolean>;
  loginWithToken: (token: string) => Promise<boolean>;
  logout: () => void;
  refreshPrincipal: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [principal, setPrincipal] = useState<AuthenticatedPrincipal | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [isDemoPreview, setIsDemoPreview] = useState<boolean>(() => {
    if (typeof window !== 'undefined') {
      const params = new URLSearchParams(window.location.search);
      const queryMode = params.get('mode');
      if (queryMode === 'demo') return true;
      if (queryMode === 'authorized') return false;

      const storedMode = sessionStorage.getItem('argus_workspace_mode');
      if (storedMode === 'demo') return true;
      if (storedMode === 'authorized') return false;

      // Default to false so direct /workspace entry presents the clean Workspace Access screen
      return false;
    }
    return false;
  });

  const fetchPrincipal = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    const token = getAuthToken();

    if (isDemoPreview) {
      setPrincipal(MOCK_PRINCIPAL);
      setIsLoading(false);
      return;
    }

    if (!token) {
      setPrincipal(null);
      setError(null);
      setIsLoading(false);
      return;
    }

    try {
      const livePrincipal = await apiClient.getMe();
      setPrincipal(livePrincipal);
      setError(null);
    } catch (err: unknown) {
      setPrincipal(null);
      if (err instanceof ApiError) {
        if (err.status === 401) {
          setError('Session Required / Token Expired');
        } else if (err.status === 403) {
          setError('Access Denied / Role Not Authorized');
        } else if (err.isNetworkError || err.status === 0) {
          setError(`Backend Unavailable: API server is not responding at ${getApiBaseUrl()}`);
        } else {
          setError(`Authentication error (${err.status}): ${err.detail}`);
        }
      } else {
        const msg = err instanceof Error ? err.message : 'Authentication verification failed.';
        setError(msg);
      }
    } finally {
      setIsLoading(false);
    }
  }, [isDemoPreview]);

  useEffect(() => {
    if (typeof window !== 'undefined') {
      const params = new URLSearchParams(window.location.search);
      const queryMode = params.get('mode');
      if (queryMode === 'demo') {
        sessionStorage.setItem('argus_workspace_mode', 'demo');
        setIsDemoPreview(true);
      } else if (queryMode === 'authorized') {
        sessionStorage.setItem('argus_workspace_mode', 'authorized');
        setIsDemoPreview(false);
      }
    }
    fetchPrincipal();
  }, [fetchPrincipal]);

  const setToken = async (token: string | null) => {
    setAuthToken(token);
    setIsDemoPreview(false);
    if (typeof sessionStorage !== 'undefined') {
      sessionStorage.setItem('argus_workspace_mode', 'authorized');
    }
    await fetchPrincipal();
  };

  const loginDevOfficer = async (credentials?: { email?: string; password?: string; role?: UserRole | string }): Promise<boolean> => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await fetch('/api/auth/dev-token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(credentials || {}),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || `Dev sign-in failed (status ${res.status})`);
      }
      const data = await res.json();
      if (!data.token) {
        throw new Error('No token returned by local dev auth service.');
      }
      await setToken(data.token);
      return true;
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Local development sign-in failed.';
      setError(msg);
      setIsLoading(false);
      return false;
    }
  };

  const loginWithToken = async (token: string): Promise<boolean> => {
    setIsLoading(true);
    setError(null);
    try {
      await setToken(token);
      return true;
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to connect token.';
      setError(msg);
      setIsLoading(false);
      return false;
    }
  };

  const logout = () => {
    setAuthToken(null);
    setPrincipal(null);
    setIsDemoPreview(false);
    setError(null);
    if (typeof sessionStorage !== 'undefined') {
      sessionStorage.removeItem('argus_workspace_mode');
    }
  };

  const enableDemoPreview = (enabled: boolean) => {
    setIsDemoPreview(enabled);
    if (typeof sessionStorage !== 'undefined') {
      sessionStorage.setItem('argus_workspace_mode', enabled ? 'demo' : 'authorized');
    }
    if (enabled) {
      setPrincipal(MOCK_PRINCIPAL);
      setError(null);
    } else {
      setPrincipal(null);
      fetchPrincipal();
    }
  };

  return (
    <AuthContext.Provider
      value={{
        principal,
        role: principal?.role ?? null,
        isLoading,
        isAuthenticated: !!principal,
        error,
        isDemoPreview,
        enableDemoPreview,
        setToken,
        loginDevOfficer,
        loginWithToken,
        logout,
        refreshPrincipal: fetchPrincipal,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextType {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
