import { createContext, useContext, type ReactNode } from 'react';

export type CopilotStatus = 'disabled' | 'loading' | 'ready' | 'unavailable';

// Kept in a dependency-free module so the disabled path never imports
// CopilotKit's client bundle or stylesheet.
export const CopilotStatusContext = createContext<CopilotStatus>('disabled');

export function CopilotStatusProvider({
  status,
  children,
}: {
  status: CopilotStatus;
  children: ReactNode;
}) {
  return (
    <CopilotStatusContext.Provider value={status}>
      {children}
    </CopilotStatusContext.Provider>
  );
}

export function useCopilotStatus() {
  return useContext(CopilotStatusContext);
}
