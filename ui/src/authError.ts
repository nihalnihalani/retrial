// The auth gate (RETRIAL_AUTH_TOKEN) is API/CLI-only — the UI ships
// unauthenticated and attaches no Authorization header (see README "Auth"). If
// an operator enables the gate anyway, every UI-initiated mutating fetch gets a
// 401; this helper makes that failure LOUD and diagnostic instead of a mystery
// "engine returned 401". Wired into the four mutating fetch sites (POST
// /tournament, POST /promote, DELETE /sandboxes/{id}, POST /sandboxes/destroy_all).
// Any other status falls through to the caller's existing message unchanged.
export const AUTH_401_MSG =
  'engine auth is on (RETRIAL_AUTH_TOKEN) — the UI is unauthenticated; ' +
  'use the CLI/API with a Bearer token or unset the env var';

export const authAware = (res: Response, fallback: string): string =>
  res.status === 401 ? AUTH_401_MSG : fallback;
