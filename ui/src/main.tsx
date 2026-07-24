import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { CopilotRoot } from './copilot/CopilotRoot';
import './index.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <CopilotRoot>
      <App />
    </CopilotRoot>
  </StrictMode>,
);
