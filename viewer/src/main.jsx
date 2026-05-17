import React, { useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import '../styles.css';
import App from './App.jsx';

function ViewerShell() {
  useEffect(() => {
    import('../viewer.js');
  }, []);

  return <App />;
}

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ViewerShell />
  </React.StrictMode>
);
