export type RecordType =
  | "A_RECORD" | "AAAA_RECORD" | "CNAME_RECORD"
  | "MX_RECORD" | "TXT_RECORD" | "SRV_RECORD";

/** Records reach the gateway two different ways.
 *  `policy` lives in the DNS store and is free-standing.
 *  `client` is the "Local DNS Record" field on a client device, invisible to
 *  the DNS API but resolved all the same. It cannot be deleted independently
 *  of the device it belongs to. */
export type RecordSource = "policy" | "client";

export interface RenderedRecord {
  id: string | null;
  source: RecordSource;
  type: RecordType;
  fqdn: string;
  label: string | null;
  value: string;
  enabled: boolean;
  ttl_seconds: number | null;
  raw: Record<string, unknown>;
  client_name?: string;
  network_name?: string | null;
  /** Client record pointing at a DHCP address rather than a reservation. */
  unstable?: boolean;
}

export interface ZoneView {
  apex: string;
  ungrouped: boolean;
  /** Single-label names like `plex`, which resolve regardless of search domain. */
  bare: boolean;
  count: number;
  records: RenderedRecord[];
}

export interface ZonesResponse {
  total: number;
  policy_count: number;
  client_count: number;
  zones: ZoneView[];
}

export interface ChangeSet {
  id: string;
  created_at: string;
  applied_at: string | null;
  summary: string;
  status: "pending" | "applied" | "failed" | "partial";
  source: string;
  author: { name: string | null; email: string | null; unifi_admin: string | null };
  reverts_id: string | null;
  error: string | null;
  revision_count: number;
  revisions?: Revision[];
}

export interface Revision {
  seq: number;
  op: "create" | "update" | "delete";
  fqdn: string;
  type: RecordType;
  unifi_id: string | null;
  applied: boolean;
  error: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
}

export interface SystemStatus {
  unifi_reachable: boolean;
  unifi_error: { status: number; code: string | null; message: string } | null;
  application_version: string | null;
  unifi_admin: string | null;
  apexes: string[];
}

export interface DriftResponse {
  clean: boolean;
  /** Mirror is empty: nothing has diverged, tracking simply has not begun. */
  first_run: boolean;
  only_on_gateway: RenderedRecord[];
  only_in_mirror: { unifi_id: string; fqdn: string; type: string; value: string }[];
  modified: { unifi_id: string; fqdn: string; mirror: unknown; gateway: unknown }[];
}

export const RECORD_TYPES: RecordType[] = [
  "A_RECORD", "AAAA_RECORD", "CNAME_RECORD", "MX_RECORD", "TXT_RECORD", "SRV_RECORD",
];

/** UniFi accepts ttlSeconds only on these; sending it elsewhere is a 400. */
export const TTL_CAPABLE = new Set<RecordType>(["A_RECORD", "AAAA_RECORD", "CNAME_RECORD"]);

export const shortType = (t: RecordType) => t.replace("_RECORD", "");


/* ------------------------------------------------------- DNS settings ---- */

export interface DnsWarning {
  network: string;
  severity: "high" | "info";
  servers: string[];
  detail: string;
}

export interface NetworkDns {
  id: string;
  name: string;
  vlan: number | string | null;
  dhcpd_dns_enabled: boolean;
  servers: string[];
  inherits_gateway: boolean;
  /** DHCP search domain: what clients append to unqualified names. */
  domain_name: string;
  domain_advice: { severity: "high" | "info"; detail: string } | null;
}

export interface DnsFilterView {
  network_id: string;
  network_name: string;
  filter: string;
  blocked_sites: string[];
  allowed_sites: string[];
  blocked_tld: string[];
}

export interface ContentFilterView {
  id: string;
  name: string;
  enabled: boolean;
  categories: string[];
  allow_list: string[];
  block_list: string[];
  client_macs: string[];
  networks: string[];
  safe_search: string[];
  schedule: string;
}

export interface DnsSettings {
  doh: {
    /** "auto" is how UniFi spells on. Validated server-side. */
    state: string;
    server_names: string[];
    custom_servers: Record<string, unknown>[];
    states: string[];
    known_providers: string[];
    /** UniFi does not validate provider identifiers; a typo is silently kept. */
    server_names_validated: boolean;
    unverified_providers: string[];
  };
  ad_blocking: {
    enabled: boolean;
    dns_filtering: boolean;
    /** ips.ad_blocking_* is a read-only projection; state is derived from profiles. */
    readonly_mirror: boolean;
    networks: { id: string; name: string; enabled: boolean; profile: string | null }[];
  };
  dns_filters: DnsFilterView[];
  /** ips.dns_filters is a legacy projection: writes to it silently do nothing. */
  dns_filters_readonly: boolean;
  content_filters: ContentFilterView[];
  categories: string[];
  traffic_logging: { gateway_dns_enabled: boolean; enabled_allowed_traffic: boolean };
  networks: NetworkDns[];
  warnings: DnsWarning[];
}

/** Public resolvers give no local records and no filtering. */
export const PUBLIC_RESOLVERS = new Set([
  "1.1.1.1", "1.0.0.1", "8.8.8.8", "8.8.4.4", "9.9.9.9", "149.112.112.112",
  "208.67.222.222", "208.67.220.220", "94.140.14.14", "94.140.15.15",
]);



/** UniFi's console shows display labels; the API returns identifiers.
 *  "Filter Scope: Ad Block" in the console is the ADVERTISEMENT category here. */
const CATEGORY_LABELS: Record<string, string> = {
  ADVERTISEMENT: "Ad Block",
  ADULT: "Adult",
  CHILD_ABUSE: "Child Abuse",
  CRYPTOMINING: "Cryptomining",
  DGA_DOMAINS: "DGA Domains",
  DNS_TUNNELING: "DNS Tunneling",
  HATE_SPEECH_AND_EXTREMISM: "Hate Speech & Extremism",
  ARTIFICIAL_INTELLIGENCE: "Artificial Intelligence",
};

export const categoryLabel = (c: string) =>
  CATEGORY_LABELS[c] ??
  c.toLowerCase().split("_").map((w) => w[0]?.toUpperCase() + w.slice(1)).join(" ");


/* ---------------------------------------------------------- migration ---- */

export interface MigrateItem {
  fqdn: string;
  kind: "A" | "AAAA" | "CNAME";
  value: string;
  source: string;
  payload: Record<string, unknown>;
  /** Present on conflicts: the value(s) already on the gateway. */
  existing?: string[];
}

export interface MigratePreview {
  source: string;
  counts: {
    imported: number; new: number; duplicate: number;
    conflict: number; shadowed: number; skipped: number;
  };
  new: MigrateItem[];
  duplicate: MigrateItem[];
  conflict: MigrateItem[];
  /** A client-bound record with the same name already answers for these. */
  shadowed: MigrateItem[];
  skipped: { line?: number; text: string; why: string }[];
}


/* ---------------------------------------------------------- dashboard ---- */

export interface StatsSummary {
  window_hours: number;
  /** DNS traffic seen from LAN clients. */
  dns_seen: number;
  /** Stopped by ad blocking or answered with a null address. */
  dns_blocked: number;
  dns_block_rate: number;
  /** Inbound perimeter drops. Counted separately: not DNS filtering. */
  perimeter_blocked: number;
  flows: number;
  events: number;
  clients: number;
  sinkholed_events: number;
  by_policy: { policy: string; events: number }[];
  coverage: { oldest: string | null; newest: string | null };
  caveat: string;
}

export interface StatsDomain { domain: string; events: number }
export interface StatsClient {
  ip: string; name: string; mac: string | null; network: string | null;
  events: number; blocked: number; block_rate: number;
}
export interface BypassDevice {
  ip: string; name: string; mac: string | null; network: string | null;
  events: number;
  methods: { kind: string; dest: string; events: number }[];
}
export interface BypassResponse {
  hours: number; gateway_ips: string[]; device_count: number; devices: BypassDevice[];
}
export interface TimelineBucket { t: string; events: number; blocked: number }


export interface RenameItem {
  old_fqdn: string; new_fqdn: string; type: RecordType;
  value: string; old_id: string | null; payload: Record<string, unknown>;
}

export interface RenamePreview {
  from_apex: string;
  to_apex: string;
  counts: { move: number; already: number; client_bound: number };
  plan: RenameItem[];
  already_exists: RenameItem[];
  /** Records living on a device; renaming these changes what the device publishes. */
  client_bound: { fqdn: string; why: string; client: string; client_id: string; suggested: string }[];
  note: string;
}


export interface AuthState {
  authenticated: boolean;
  user: { sub?: string; name?: string; email?: string } | null;
  /** "open" means no auth backend is configured and the app is unprotected. */
  mode: "oidc" | "forward-auth" | "open";
  authorization: "delegated";
}
