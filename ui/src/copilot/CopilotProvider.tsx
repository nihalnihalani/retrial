import type { ReactNode } from 'react';
import { CopilotKit } from '@copilotkit/react-core/v2';
import '@copilotkit/react-core/v2/styles.css';
import { CopilotStatusProvider } from './CopilotStatusProvider';

export function CopilotProvider({ children }: { children: ReactNode }) {
  return (
    <CopilotKit
      runtimeUrl="/api/copilotkit"
      useSingleEndpoint
      enableInspector={false}
      showDevConsole={false}
    >
      <CopilotStatusProvider status="ready">{children}</CopilotStatusProvider>
    </CopilotKit>
  );
}
