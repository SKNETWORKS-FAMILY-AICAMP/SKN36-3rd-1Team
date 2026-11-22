import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { ScheduleClock } from './components/ScheduleClock';
import { SavedPoliciesProvider } from './components/SavedPolicies';
import './styles.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ScheduleClock><SavedPoliciesProvider><App /></SavedPoliciesProvider></ScheduleClock>
  </StrictMode>,
);
