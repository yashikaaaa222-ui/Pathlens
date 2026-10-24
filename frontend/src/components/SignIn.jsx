import { useState } from 'react';
import { useAuth } from '../lib/auth.jsx';

export default function SignIn({ reason }) {
  const { supabaseEnabled, signIn, signUp } = useAuth();
  const [mode, setMode] = useState('in');
  const [f, setF] = useState({ email: '', password: '' });
  const [msg, setMsg] = useState({ error: '', info: '', busy: false });

  if (!supabaseEnabled) {
    return (
      <p className="error" role="alert">
        {reason} Sign-in is not set up in this build. Add VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY to the frontend settings.
      </p>
    );
  }

  async function submit(e) {
    e.preventDefault();
    setMsg({ error: '', info: '', busy: true });
    try {
      if (mode === 'in') await signIn(f.email.trim(), f.password);
      else {
        const r = await signUp(f.email.trim(), f.password);
        if (r.needsConfirmation) return setMsg({ error: '', info: 'Check your email and open the confirmation link, then sign in.', busy: false });
      }
      setMsg({ error: '', info: '', busy: false });
    } catch (err) {
      setMsg({ error: err.message, info: '', busy: false });
    }
  }

  return (
    <form className="search" onSubmit={submit}>
      <h2>{mode === 'in' ? 'Sign in' : 'Create an account'}</h2>
      {reason && <p className="muted">{reason}</p>}
      <label>
        Email
        <input type="email" autoComplete="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} />
      </label>
      <label>
        Password
        <input type="password" autoComplete={mode === 'in' ? 'current-password' : 'new-password'} required minLength={8} value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} />
      </label>
      {msg.error && <p className="error" role="alert">{msg.error}</p>}
      {msg.info && <p className="ok" role="status">{msg.info}</p>}
      <div className="row">
        <button className="primary" disabled={msg.busy}>{mode === 'in' ? 'Sign in' : 'Create account'}</button>
        <button type="button" className="ghost" onClick={() => { setMode(mode === 'in' ? 'up' : 'in'); setMsg({ error: '', info: '', busy: false }); }}>
          {mode === 'in' ? 'Create an account instead' : 'I already have an account'}
        </button>
      </div>
      <small className="muted">Your reports are linked to an account id, never shown publicly. Your email is not stored by PathLens.</small>
    </form>
  );
}
