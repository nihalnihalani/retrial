import {
  Component,
  lazy,
  Suspense,
  useEffect,
  useState,
  type ReactNode,
} from 'react';
import { CopilotStatusProvider } from './CopilotStatusProvider';

const CopilotProvider = lazy(() =>
  import('./CopilotProvider').then((module) => ({ default: module.CopilotProvider })),
);

interface CopilotErrorBoundaryProps {
  children: ReactNode;
  fallback: ReactNode;
}

interface CopilotErrorBoundaryState {
  failed: boolean;
}

class CopilotErrorBoundary extends Component<
  CopilotErrorBoundaryProps,
  CopilotErrorBoundaryState
> {
  state: CopilotErrorBoundaryState = { failed: false };

  static getDerivedStateFromError(): CopilotErrorBoundaryState {
    return { failed: true };
  }

  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

function copilotFeatureRequested() {
  // Vite only statically replaces direct import.meta.env property access.
  // Aliasing import.meta makes this silently read as undefined in the browser.
  return import.meta.env.VITE_COPILOT_ENABLED === '1';
}

function EnabledCopilotRoot({ children }: { children: ReactNode }) {
  const [runtimeStatus, setRuntimeStatus] = useState<'loading' | 'ready' | 'unavailable'>(
    'loading',
  );
  const unavailable = (
    <CopilotStatusProvider status="unavailable">{children}</CopilotStatusProvider>
  );
  const loading = <CopilotStatusProvider status="loading">{children}</CopilotStatusProvider>;

  useEffect(() => {
    let active = true;
    let activeController: AbortController | null = null;
    let interval: number | null = null;
    // Keep polling after a failure. The sidecar is a separate process, so it can
    // come back at any time; giving up here would strand the board in `offline`
    // until a manual reload.
    const markUnavailable = () => {
      if (active) setRuntimeStatus('unavailable');
    };
    const checkRuntime = async () => {
      const controller = new AbortController();
      activeController = controller;
      const timeout = window.setTimeout(() => controller.abort(), 2_500);
      try {
        const response = await fetch('/api/copilotkit/healthz', {
          cache: 'no-store',
          headers: { accept: 'application/json' },
          signal: controller.signal,
        });
        const body = response.ok
          ? ((await response.json()) as { configured?: unknown })
          : null;
        if (body?.configured === true) {
          if (active) setRuntimeStatus('ready');
        } else {
          markUnavailable();
        }
      } catch {
        markUnavailable();
      } finally {
        window.clearTimeout(timeout);
        if (activeController === controller) activeController = null;
      }
    };

    void checkRuntime();
    interval = window.setInterval(() => void checkRuntime(), 5_000);

    return () => {
      active = false;
      if (interval !== null) window.clearInterval(interval);
      activeController?.abort();
    };
  }, []);

  if (runtimeStatus === 'loading') return loading;
  if (runtimeStatus === 'unavailable') return unavailable;

  return (
    <CopilotErrorBoundary fallback={unavailable}>
      <Suspense fallback={loading}>
        <CopilotProvider>{children}</CopilotProvider>
      </Suspense>
    </CopilotErrorBoundary>
  );
}

export function CopilotRoot({ children }: { children: ReactNode }) {
  if (!copilotFeatureRequested()) {
    return <CopilotStatusProvider status="disabled">{children}</CopilotStatusProvider>;
  }

  return <EnabledCopilotRoot>{children}</EnabledCopilotRoot>;
}
