# Security notes

## The UniFi API key is root-equivalent

`unifi-dns` needs a UniFi Network API key. Treat that key as a **root
credential for the gateway**, not as a scoped read token.

The UniFi settings endpoint (`/api/s/{site}/rest/setting`) returns the `mgmt`
document to any holder of a valid API key. That document contains, in cleartext:

- `x_ssh_username` and `x_ssh_password`
- `x_ssh_md5passwd` and `x_ssh_sha512passwd`
- `x_api_token`
- `x_mgmt_key`
- the wireless mesh pre-shared key

This is UniFi's behaviour, not something this project introduces or can work
around. It means anyone who obtains the key can obtain shell on the gateway.

**What this project does about it**

- The key is read from the environment and held in server memory only.
- It is never returned to the browser, never written to the database, and never
  included in an error response or log line.
- Authentication for *users* is deliberately separate (OIDC or a trusted
  forward-auth header), so the gateway credential never doubles as a login.

**What you should do about it**

- Give the container its own API key rather than reusing a personal one, and
  rotate it independently.
- Do not commit `.env`. The shipped `.gitignore` excludes it.
- Keep the deployment behind an authenticating proxy or on a trusted network.
- If your gateway has SSH enabled with password auth, add a key and disable
  password login. The plaintext exposure above makes a weak SSH password a
  direct path to shell.

## Reporting a vulnerability

Open a private security advisory through GitHub rather than a public issue.

## Scope

This tool writes DNS records and DNS-related settings to your gateway. It does
not modify firewall rules, routing, or client configuration beyond the DHCP
resolver and search domain fields it explicitly exposes.
