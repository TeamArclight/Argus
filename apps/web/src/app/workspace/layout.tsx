import React from 'react';
import { AuthProvider } from '@/hooks/useAuth';
import { WorkspaceLayout } from '@/components/layout/WorkspaceLayout';

export default function AppWorkspaceLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <AuthProvider>
      <WorkspaceLayout>{children}</WorkspaceLayout>
    </AuthProvider>
  );
}
