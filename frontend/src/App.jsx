import { NavLink, Navigate, Route, Routes } from 'react-router-dom';
import Home from './pages/Home.jsx';
import Explore from './pages/Explore.jsx';
import Admin from './pages/Admin.jsx';
import Account from './pages/Account.jsx';
import { useAuth } from './lib/auth.jsx';
import { useServer } from './lib/server.jsx';

export default function App() {
  const { account, supabaseEnabled } = useAuth();
  const { loginRequired } = useServer();
  return (
    <div className="shell">
      <header className="nav">
        <NavLink to="/" className="brand">
          <img src="/images/logo.svg" alt="" width="26" height="26" />
          PathLens
        </NavLink>
        <nav aria-label="Main">
          <NavLink to="/explore">Explore</NavLink>
          <NavLink to="/admin">Admin</NavLink>
          {(supabaseEnabled || loginRequired) && (
            <NavLink to="/account">{account ? 'Account' : 'Sign in'}</NavLink>
          )}
        </nav>
      </header>
      <main className="page">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/explore" element={<Explore />} />
          <Route path="/admin" element={<Admin />} />
          <Route path="/account" element={<Account />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
