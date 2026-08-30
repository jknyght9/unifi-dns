import type {
  AuthState, BypassResponse, ChangeSet, DnsSettings, DriftResponse, MigratePreview,
  RenamePreview, StatsClient, StatsDomain, StatsSummary, SystemStatus,
  TimelineBucket, ZonesResponse,
} from "./types";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    // Read the body exactly once. A Response body is a single-use stream, so
    // calling res.json() and then falling back to res.text() throws
    // "Body has already been consumed" and hides the actual error.
    const raw = await res.text();
    let detail: unknown = raw;
    try {
      const parsed = JSON.parse(raw);
      detail = parsed?.detail ?? parsed;
    } catch {
      // Not JSON (nginx HTML error page, proxy timeout). Keep the raw text.
    }
    const msg =
      typeof detail === "string"
        ? detail
        : (detail as { message?: string })?.message ?? JSON.stringify(detail);
    throw new Error(msg?.slice(0, 500) || `HTTP ${res.status} ${res.statusText}`);
  }
  if (res.status === 204) return undefined as T;
  const raw = await res.text();
  if (!raw) return undefined as T;
  try {
    return JSON.parse(raw) as T;
  } catch {
    throw new Error(`Expected JSON from ${path}, got: ${raw.slice(0, 200)}`);
  }
}

export const api = {
  status: () => req<SystemStatus>("/system/status"),
  me: () => req<AuthState>("/auth/me"),
  logout: () => req<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  migratePreview: (body: unknown) =>
    req<MigratePreview>("/migrate/preview", { method: "POST", body: JSON.stringify(body) }),
  renamePreview: (from_apex: string, to_apex: string) =>
    req<RenamePreview>("/migrate/rename/preview", {
      method: "POST", body: JSON.stringify({ from_apex, to_apex }),
    }),
  renameApply: (records: unknown[], note?: string) =>
    req<ChangeSet>("/migrate/rename/apply", {
      method: "POST", body: JSON.stringify({ records, note }),
    }),
  removeRecords: (ids: string[], note?: string) =>
    req<ChangeSet>("/migrate/remove", {
      method: "POST", body: JSON.stringify({ records: ids.map((id) => ({ id })), note }),
    }),
  migrateApply: (records: unknown[], ttl: number, note?: string) =>
    req<ChangeSet>("/migrate/apply", {
      method: "POST", body: JSON.stringify({ records, ttl, note }),
    }),
  zones: () => req<ZonesResponse>("/records?group=true"),
  apexes: () => req<{ id: string; name: string; description: string | null }[]>("/apexes"),
  suggestApexes: () => req<{ suggestions: string[] }>("/apexes/suggest"),
  addApex: (name: string) =>
    req<{ id: string; name: string }>("/apexes", {
      method: "POST", body: JSON.stringify({ name }),
    }),
  removeApex: (id: string) => req<void>(`/apexes/${id}`, { method: "DELETE" }),

  createRecord: (record: unknown, note?: string) =>
    req<ChangeSet>("/records", { method: "POST", body: JSON.stringify({ record, note }) }),
  updateRecord: (id: string, record: unknown, note?: string) =>
    req<ChangeSet>(`/records/${id}`, { method: "PUT", body: JSON.stringify({ record, note }) }),
  deleteRecord: (id: string) => req<ChangeSet>(`/records/${id}`, { method: "DELETE" }),

  setClientRecord: (clientId: string, hostname: string | null, enabled?: boolean) =>
    req<ChangeSet>(`/client-records/${clientId}`, {
      method: "PUT", body: JSON.stringify({ hostname, enabled }),
    }),

  changesets: () => req<ChangeSet[]>("/changesets"),
  changeset: (id: string) => req<ChangeSet>(`/changesets/${id}`),
  rollback: (id: string, dryRun: boolean) =>
    req<{ plan?: unknown[]; applied: boolean } & Partial<ChangeSet>>(
      `/changesets/${id}/rollback?dry_run=${dryRun}`, { method: "POST" },
    ),

  dnsSettings: () => req<DnsSettings>("/settings/dns"),
  setDoh: (body: Record<string, unknown>) =>
    req<unknown>("/settings/doh", { method: "PUT", body: JSON.stringify(body) }),
  setAdBlocking: (body: { enabled?: boolean; network_ids?: string[] }) =>
    req<unknown>("/settings/ad-blocking", { method: "PUT", body: JSON.stringify(body) }),
  setContentFilter: (id: string, body: Record<string, unknown>) =>
    req<unknown>(`/settings/content-filter/${id}`, {
      method: "PUT", body: JSON.stringify(body),
    }),
  setTrafficFlow: (body: { gateway_dns_enabled?: boolean; enabled_allowed_traffic?: boolean }) =>
    req<unknown>("/settings/traffic-flow", { method: "PUT", body: JSON.stringify(body) }),
  setNetworkDns: (
    id: string, dhcpd_dns_enabled: boolean, servers: string[], domain_name?: string,
  ) =>
    req<unknown>(`/settings/networks/${id}/dns`, {
      method: "PUT",
      body: JSON.stringify({ dhcpd_dns_enabled, servers, domain_name }),
    }),

  statsSummary: (h: number) => req<StatsSummary>(`/stats/summary?hours=${h}`),
  statsDomains: (h: number, blocked: boolean, limit = 15) =>
    req<{ domains: StatsDomain[] }>(`/stats/domains?hours=${h}&blocked=${blocked}&limit=${limit}`),
  statsClients: (h: number, limit = 12) =>
    req<{ clients: StatsClient[] }>(`/stats/clients?hours=${h}&limit=${limit}`),
  statsTimeline: (h: number) => req<{ buckets: TimelineBucket[] }>(`/stats/timeline?hours=${h}`),
  statsBypass: (h: number) => req<BypassResponse>(`/stats/bypass?hours=${h}`),
  statsCollect: () => req<{ stored: number }>("/stats/collect", { method: "POST" }),

  drift: () => req<DriftResponse>("/drift"),
  adopt: () => req<{ synced: number }>("/drift/adopt", { method: "POST" }),
};
