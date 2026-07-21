/// <reference types="vite/client" />

/**
 * Build-time configuration, injected by Vite from the environment.
 *
 * These are public values baked into the bundle, so nothing secret belongs
 * here: the Cognito domain and the public client id are both safe to ship
 * (the client has no secret — see web/src/auth.ts). When they are unset the
 * app runs unauthenticated, which is the local-development path.
 */
interface ImportMetaEnv {
  readonly VITE_COGNITO_DOMAIN?: string;
  readonly VITE_COGNITO_CLIENT_ID?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
