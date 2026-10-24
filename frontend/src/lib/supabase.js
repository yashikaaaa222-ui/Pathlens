// Supabase is optional. Without these two public values the site still works: reading is open, and reporting
// needs no account unless the server says it does (in which case people are told sign-in is not set up).
// The anon key is meant to be public. Never put a service-role key or a JWT secret in the frontend.
const URL = (typeof import.meta !== 'undefined' && import.meta.env && import.meta.env.VITE_SUPABASE_URL) || '';
const KEY = (typeof import.meta !== 'undefined' && import.meta.env && import.meta.env.VITE_SUPABASE_ANON_KEY) || '';

export const supabaseEnabled = Boolean(URL && KEY);

let client = null;
// Loaded on first use, so the library is not downloaded at all when sign-in is not configured.
export async function getSupabase() {
  if (!supabaseEnabled) return null;
  if (!client) {
    const { createClient } = await import('@supabase/supabase-js');
    client = createClient(URL, KEY, { auth: { persistSession: true, autoRefreshToken: true } });
  }
  return client;
}
