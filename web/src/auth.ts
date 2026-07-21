/**
 * Cognito sign-in, authorization code flow with PKCE.
 *
 * No client secret exists, because a secret shipped to a browser is not a
 * secret. PKCE closes the gap that leaves: the app commits to a random
 * verifier up front, and the code it receives back is worthless to anyone who
 * cannot produce that verifier.
 *
 * Tokens live in localStorage rather than memory so an installed PWA survives
 * being swiped away, and rather than a cookie because the API is called from
 * script and there is no CSRF surface to defend. The trade is that XSS could
 * read them — which is why `markdown.tsx` renders the model's output without
 * `dangerouslySetInnerHTML`.
 */

const STORAGE_KEY = "gg-session";
const VERIFIER_KEY = "gg-pkce-verifier";

export interface AuthConfig {
  domain: string; // https://<prefix>.auth.<region>.amazoncognito.com
  clientId: string;
  redirectUri: string;
}

interface Session {
  idToken: string;
  refreshToken: string;
  /** Epoch ms. Refreshed slightly early so a call never races expiry. */
  expiresAt: number;
}

/** Build-time config. Empty when unset, which means "auth is off". */
export const authConfig: AuthConfig | null = import.meta.env.VITE_COGNITO_DOMAIN
  ? {
      domain: import.meta.env.VITE_COGNITO_DOMAIN as string,
      clientId: import.meta.env.VITE_COGNITO_CLIENT_ID as string,
      redirectUri: window.location.origin + "/",
    }
  : null;

// --- PKCE ------------------------------------------------------------------

function randomVerifier(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return base64Url(bytes);
}

function base64Url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

async function challengeFor(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64Url(new Uint8Array(digest));
}

/**
 * SHA-256 of a request body, hex encoded.
 *
 * Required on every POST that goes through CloudFront to the Lambda function
 * URL: origin access control signs with SigV4, and Lambda rejects unsigned
 * payloads, so the browser must supply the payload hash as
 * `x-amz-content-sha256`. Without it the request fails with a signature
 * mismatch that says nothing about the real cause.
 */
export async function payloadHash(body: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(body));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// --- session ---------------------------------------------------------------

function load(): Session | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

function save(session: Session): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
}

export function signOut(): void {
  localStorage.removeItem(STORAGE_KEY);
  if (authConfig) {
    const url = new URL(`${authConfig.domain}/logout`);
    url.searchParams.set("client_id", authConfig.clientId);
    url.searchParams.set("logout_uri", authConfig.redirectUri);
    window.location.assign(url.toString());
  }
}

/** Send the browser to the hosted UI. Never returns. */
export async function signIn(): Promise<void> {
  if (!authConfig) return;
  const verifier = randomVerifier();
  sessionStorage.setItem(VERIFIER_KEY, verifier);

  const url = new URL(`${authConfig.domain}/oauth2/authorize`);
  url.searchParams.set("client_id", authConfig.clientId);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("scope", "openid email profile");
  url.searchParams.set("redirect_uri", authConfig.redirectUri);
  url.searchParams.set("code_challenge", await challengeFor(verifier));
  url.searchParams.set("code_challenge_method", "S256");
  window.location.assign(url.toString());
}

async function exchange(params: Record<string, string>): Promise<Session | null> {
  if (!authConfig) return null;
  const response = await fetch(`${authConfig.domain}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ client_id: authConfig.clientId, ...params }),
  });
  if (!response.ok) return null;

  const data = (await response.json()) as {
    id_token: string;
    refresh_token?: string;
    expires_in: number;
  };
  const session: Session = {
    idToken: data.id_token,
    // A refresh response omits the refresh token; keep the existing one.
    refreshToken: data.refresh_token ?? load()?.refreshToken ?? "",
    expiresAt: Date.now() + data.expires_in * 1000,
  };
  save(session);
  return session;
}

/**
 * Complete the redirect back from the hosted UI, if that is why we are here.
 * Returns true when a sign-in was consumed, so the caller can clean the URL.
 */
export async function completeSignIn(): Promise<boolean> {
  const code = new URLSearchParams(window.location.search).get("code");
  if (!code || !authConfig) return false;

  const verifier = sessionStorage.getItem(VERIFIER_KEY) ?? "";
  sessionStorage.removeItem(VERIFIER_KEY);

  await exchange({ grant_type: "authorization_code", code, code_verifier: verifier });

  // Drop ?code= so a refresh does not try to redeem a spent code.
  window.history.replaceState({}, "", window.location.pathname);
  return true;
}

/**
 * A valid ID token, refreshing if needed, or null when signed out.
 *
 * The 60-second margin matters on a phone: a token that passes the check and
 * then expires in flight would surface as a spurious sign-out.
 */
export async function currentToken(): Promise<string | null> {
  if (!authConfig) return null;

  const session = load();
  if (!session) return null;
  if (Date.now() < session.expiresAt - 60_000) return session.idToken;

  if (!session.refreshToken) return null;
  const refreshed = await exchange({
    grant_type: "refresh_token",
    refresh_token: session.refreshToken,
  });
  return refreshed?.idToken ?? null;
}

export function isSignedIn(): boolean {
  return authConfig === null || load() !== null;
}
