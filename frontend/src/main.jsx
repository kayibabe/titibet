import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import { AuthProvider } from './context/AuthContext.jsx'

// Service worker — production only.
// In development (Vite HMR), service workers cause stale-cache headaches:
// any previously-registered SW keeps serving old cached assets even after code
// changes.  We unregister all SWs in dev so the browser always fetches fresh.
if ('serviceWorker' in navigator) {
  if (import.meta.env.DEV) {
    // Unregister any lingering service workers in development.
    navigator.serviceWorker.getRegistrations().then(regs => {
      regs.forEach(reg => reg.unregister())
    })
  } else {
    // Production: register the SW and reload when a new version takes over.
    navigator.serviceWorker.register('/sw.js').catch(() => {})
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      window.location.reload()
    })
  }
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <AuthProvider>
      <App />
    </AuthProvider>
  </StrictMode>,
)
