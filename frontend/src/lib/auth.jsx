import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { api, setCredentials } from './api.js';
import { getSupabase, supabaseEnabled } from './supabase.js';

const AuthContext = createContext(null);
export const useAuth = () => useContext(AuthContext);

// Who is using the site: an optional account (Supabase) and, for moderators, optionally the shared admin key.
// The admin key lives in memory only, so a refresh forgets it. A moderator account stays signed in.
// "moderator" is what the SERVER says (it accepts or refuses the credentials), never a guess from the browser:
//   unknown (nothing to check) | checking | yes | no | off (admin switched off on the server) | unreachable
export function AuthProvider({ children }) {
  const [session, setSession] = useState(null);
  const [adminToken, setAdminToken] = useState('');
  const [moderator, setModerator] = useState('unknown');
  const current = useRef({});
  current.current = { bearer: session ? session.access_token : undefined, adminToken: adminToken || undefined };
  const checkId = useRef(0);

  useEffect(() => {
    setCredentials(() => current.current);
  }, []);

  useEffect(() => {
    let alive = true;
    let unsubscribe = () => {};
    getSupabase().then((sb) => {
      if (!sb || !alive) return;
      sb.auth.getSession().then(({ data }) => alive && setSession(data.session));
      const { data } = sb.auth.onAuthStateChange((_event, next) => setSession(next));
      unsubscribe = () => data.subscription.unsubscribe();
    });
    return () => {
      alive = false;
      unsubscribe();
    };
  }, []);

  const recheck = useCallback(async () => {
    const id = ++checkId.current;
    if (!current.current.bearer && !current.current.adminToken) return setModerator('unknown');
    setModerator('checking');
    let result = 'yes';
    try {
      await api.stats();
    } catch (err) {
      result = err.status === 503 ? 'off' : err.status === 0 ? 'unreachable' : 'no';
    }
    if (id === checkId.current) setModerator(result);
  }, []);

  const token = session ? session.access_token : '';
  useEffect(() => {
    recheck();
  }, [token, adminToken, recheck]);

  const value = useMemo(() => {
    const need = async () => {
      const sb = await getSupabase();
      if (!sb) throw new Error('Sign-in is not set up in this build.');
      return sb;
    };
    return {
      supabaseEnabled,
      account: session && session.user ? { id: session.user.id, email: session.user.email || '' } : null,
      adminToken,
      setAdminToken,
      moderator,
      recheck,
      async signIn(email, password) {
        const { error } = await (await need()).auth.signInWithPassword({ email, password });
        if (error) throw new Error(error.message);
      },
      async signUp(email, password) {
        const { data, error } = await (await need()).auth.signUp({ email, password });
        if (error) throw new Error(error.message);
        return { needsConfirmation: !data.session };
      },
      async signOut() {
        const sb = await getSupabase();
        if (sb) await sb.auth.signOut();
      },
    };
  }, [session, adminToken, moderator, recheck]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
