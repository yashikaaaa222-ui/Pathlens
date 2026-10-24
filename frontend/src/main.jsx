import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App.jsx';
import { AuthProvider } from './lib/auth.jsx';
import { ServerProvider } from './lib/server.jsx';
import './index.css';

createRoot(document.getElementById('root')).render(
  <BrowserRouter>
    <ServerProvider>
      <AuthProvider>
        <App />
      </AuthProvider>
    </ServerProvider>
  </BrowserRouter>,
);
