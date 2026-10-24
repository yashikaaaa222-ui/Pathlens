import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { api } from './api.js';

const ServerContext = createContext(null);
export const useServer = () => useContext(ServerContext);

// What the server says about itself: whether it is up, whether sign-in is required, the privacy notice shown
// next to the consent box, the category labels and the persona presets. All of it comes from /api/methodology,
// so the interface never keeps its own copy that could drift from the engine.
export function ServerProvider({ children }) {
  const [state, setState] = useState({ status: 'loading', error: '', methodology: null });

  const load = useCallback(async () => {
    setState((s) => ({ ...s, status: 'loading', error: '' }));
    try {
      const [, methodology] = await Promise.all([api.health(), api.methodology()]);
      setState({ status: 'ready', error: '', methodology });
    } catch (err) {
      setState({ status: 'down', error: err.message, methodology: null });
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const value = useMemo(() => {
    const m = state.methodology;
    return {
      status: state.status,
      error: state.error,
      retry: load,
      methodology: m,
      loginRequired: Boolean(m && m.accounts && m.accounts.login_required_to_report),
      privacy: (m && m.privacy) || [],
      categories: (m && m.categories) || {},
      personas: (m && m.personas) || {},
    };
  }, [state, load]);

  return <ServerContext.Provider value={value}>{children}</ServerContext.Provider>;
}
