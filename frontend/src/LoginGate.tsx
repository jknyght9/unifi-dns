import type { AuthState } from "./types";

const Mark = () => (
  <svg className="mark" viewBox="0 0 32 32" aria-hidden="true">
    <rect width="32" height="32" rx="7" fill="#006FFF" />
    <g stroke="#fff" strokeWidth="3" strokeLinecap="round" fill="none">
      <path d="M9.5 16h4.5" />
      <path d="M14 16c4 0 3.5-6.5 7.5-6.5" />
      <path d="M14 16c4 0 3.5 6.5 7.5 6.5" />
    </g>
    <g fill="#fff">
      <circle cx="9" cy="16" r="3.4" />
      <circle cx="22.5" cy="9.5" r="3" />
      <circle cx="22.5" cy="22.5" r="3" />
    </g>
  </svg>
);

/**
 * Shown when the API rejects us as unauthenticated.
 *
 * What the user signs into is *this application*, with an external identity
 * provider as the authority. There is no local account database, and no role
 * model: whoever the provider admits gets full access, so restricting who may
 * reach this app is done in the provider.
 */
export function LoginGate({ auth }: { auth: AuthState }) {
  const oidc = auth.mode === "oidc";
  return (
    <div className="gate">
      <div className="gate-card">
        <Mark />
        <h1>unifi-dns</h1>
        {oidc ? (
          <>
            <p>
              Sign in with your identity provider to manage DNS on this gateway.
            </p>
            <a className="btn primary" href="/api/auth/login">Sign in</a>
          </>
        ) : (
          <>
            <p>
              This deployment expects an authenticating proxy in front of it, and
              the request arrived without an identity header.
            </p>
            <div className="gate-note">
              Check that the proxy is passing the header named by{" "}
              <code>TRUSTED_USER_HEADER</code>, and that this app is not reachable
              directly, bypassing it.
            </div>
          </>
        )}
        <div className="gate-note">
          You are signing in to <strong>unifi-dns</strong>, not to UniFi. Access is
          granted by your identity provider; this application has no accounts and
          no roles of its own.
        </div>
      </div>
    </div>
  );
}
