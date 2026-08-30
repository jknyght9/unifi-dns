import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import { categoryLabel, PUBLIC_RESOLVERS } from "./types";
import type { ContentFilterView, NetworkDns } from "./types";

/** Comma or newline separated list <-> array, for the block/allow list editors. */
const toList = (s: string) =>
  s.split(/[\s,]+/).map((x) => x.trim().toLowerCase()).filter(Boolean);

export function DnsSettingsPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["dnsSettings"], queryFn: api.dnsSettings });
  const [err, setErr] = useState<string | null>(null);
  const [editingNet, setEditingNet] = useState<NetworkDns | null>(null);
  const [editingProfile, setEditingProfile] = useState<ContentFilterView | null>(null);

  const done = () => {
    setErr(null); setEditingNet(null); setEditingProfile(null);
    qc.invalidateQueries({ queryKey: ["dnsSettings"] });
  };
  const fail = (e: Error) => setErr(e.message);

  const adblock = useMutation({ mutationFn: api.setAdBlocking, onSuccess: done, onError: fail });
  const flow = useMutation({ mutationFn: api.setTrafficFlow, onSuccess: done, onError: fail });
  const doh = useMutation({ mutationFn: api.setDoh, onSuccess: done, onError: fail });
  const netDns = useMutation({
    mutationFn: ({ id, enabled, servers, domain }:
      { id: string; enabled: boolean; servers: string[]; domain: string }) =>
      api.setNetworkDns(id, enabled, servers, domain),
    onSuccess: done, onError: fail,
  });
  const contentFilter = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) =>
      api.setContentFilter(id, body),
    onSuccess: done, onError: fail,
  });

  const d = q.data;
  if (!d) return <><h1>DNS Settings</h1><div className="sub">Loading...</div></>;

  return (
    <>
      <h1>DNS Settings</h1>
      <div className="sub">
        UniFi spreads these across four settings pages and three APIs. Whether any of
        the filtering below does anything depends on the resolver each network hands out.
      </div>
      {err && <div className="banner err">{err}</div>}

      {d.warnings.map((w, i) => (
        <div key={i} className={`banner ${w.severity === "high" ? "err" : ""}`}>
          <strong>{w.network}</strong> · {w.detail}
        </div>
      ))}

      {/* ---- resolver assignment: the control everything else depends on ---- */}
      <div className="card">
        <div className="card-head">
          <h2>Per-network DNS</h2>
          <span className="pill">{d.networks.length}</span>
          {d.networks.some((n) => n.domain_advice?.severity === "high") && (
            <span className="pill warn">
              {d.networks.filter((n) => n.domain_advice?.severity === "high").length} search
              domains collide with mDNS
            </span>
          )}
        </div>
        <table>
          <thead><tr>
            <th>Network</th><th style={{ width: 60 }}>VLAN</th>
            <th>Resolver handed out</th>
            <th style={{ width: "26%" }}>Search domain</th>
            <th style={{ width: 80 }} />
          </tr></thead>
          <tbody>
            {d.networks.map((n) => {
              const pub = n.servers.filter((s) => PUBLIC_RESOLVERS.has(s));
              return (
                <tr key={n.id}>
                  <td>{n.name}</td>
                  <td className="dim mono">{n.vlan ?? "—"}</td>
                  <td className="mono">
                    {n.inherits_gateway
                      ? <span className="dim">inherits gateway</span>
                      : n.servers.map((s) => (
                          <span key={s} className={`tag ${PUBLIC_RESOLVERS.has(s) ? "warn" : ""}`}
                                style={{ marginRight: 6 }}>{s}</span>
                        ))}
                    {pub.length > 0 && (
                      <span className="dim" style={{ marginLeft: 6, fontSize: 12 }}>
                        public resolver, bypasses filtering
                      </span>
                    )}
                  </td>
                  <td className="mono">
                    {n.domain_name || <span className="dim">none</span>}
                    {n.domain_advice && (
                      <span className={`tag ${n.domain_advice.severity === "high" ? "warn" : ""}`}
                            style={{ marginLeft: 6 }} title={n.domain_advice.detail}>
                        {n.domain_advice.severity === "high" ? "mDNS conflict" : "unreserved"}
                      </span>
                    )}
                  </td>
                  <td style={{ textAlign: "right" }}>
                    <button className="btn sm" onClick={() => setEditingNet(n)}>Edit</button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* ---- ad blocking ---- */}
      <div className="card">
        <div className="card-head">
          <h2>Ad blocking</h2>
          <span className="pill">
            {d.ad_blocking.networks.filter((n) => n.enabled).length} networks
          </span>
        </div>
        <table>
          <tbody>
            {d.ad_blocking.networks.filter((n) => n.profile || n.enabled).map((n) => (
              <tr key={n.id}>
                <td>{n.name} <span className="dim" style={{ fontSize: 12 }}>
                  via profile {n.profile}</span></td>
                <td style={{ width: 100 }}>
                  <span className={`tag ${n.enabled ? "" : "dim"}`}>
                    {n.enabled ? "blocking" : "off"}
                  </span>
                </td>
                <td style={{ textAlign: "right", width: 110 }}>
                  <button className="btn sm" disabled={adblock.isPending}
                          onClick={() => {
                            const ids = d.ad_blocking.networks.filter((x) =>
                              x.id === n.id ? !x.enabled : x.enabled).map((x) => x.id);
                            adblock.mutate({ network_ids: ids });
                          }}>
                    {n.enabled ? "Turn off" : "Turn on"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div style={{ padding: "0 14px 14px" }} className="dim">
          There is no standalone ad blocking API. What the UniFi console calls
          <strong> Filter Scope: Ad Block</strong> is the <code>ADVERTISEMENT</code>{" "}
          category on that network's content-filtering profile, which is why the same
          setting appears in both places. The <code>ips.ad_blocking_*</code> fields look
          authoritative but are a read-only mirror: writes to them report success and
          change nothing, so this state is derived from the profiles instead.
        </div>
      </div>

      {/* ---- legacy per-network filter view, read-only ---- */}
      <div className="card">
        <div className="card-head">
          <h2>Per-network filter levels (legacy view)</h2>
          <span className="pill">read only</span>
        </div>
        <table>
          <thead><tr>
            <th>Network</th><th style={{ width: 90 }}>Level</th>
            <th style={{ width: 90 }}>Blocked</th><th style={{ width: 90 }}>Allowed</th>
            <th style={{ width: 80 }}>TLDs</th>
          </tr></thead>
          <tbody>
            {d.dns_filters.map((f) => (
              <tr key={f.network_id}>
                <td>{f.network_name}</td>
                <td><span className={`tag ${f.filter === "none" ? "dim" : ""}`}>{f.filter}</span></td>
                <td className="mono">{f.blocked_sites.length}</td>
                <td className="mono">{f.allowed_sites.length}</td>
                <td className="mono">{f.blocked_tld.length}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div style={{ padding: "0 14px 14px" }} className="dim">
          This mirrors <code>ips.dns_filters</code>, which UniFi keeps for compatibility.
          Writing to it returns success and changes nothing, so it is shown but not
          editable. Use the profiles below, which is where these values actually live.
        </div>
      </div>

      {/* ---- content filtering profiles: the real filtering controls ---- */}
      <div className="card">
        <div className="card-head">
          <h2>Content filtering profiles</h2>
          <span className="pill">{d.content_filters.length}</span>
          <span className="pill">{d.categories.length} categories available</span>
        </div>
        <table>
          <thead><tr>
            <th>Profile</th><th>Networks</th><th>Categories</th>
            <th style={{ width: 80 }}>Devices</th><th style={{ width: 80 }}>Blocked</th>
            <th style={{ width: 90 }}>Schedule</th><th style={{ width: 80 }} />
          </tr></thead>
          <tbody>
            {d.content_filters.map((p) => (
              <tr key={p.id} className={p.enabled ? "" : "disabled-row"}>
                <td>{p.name} {!p.enabled && <span className="tag">off</span>}</td>
                <td className="dim">{p.networks.join(", ") || "—"}</td>
                <td>{p.categories.map((c) => (
                  <span key={c} className="tag" style={{ marginRight: 4 }}
                        title={c}>{categoryLabel(c)}</span>))}</td>
                <td className="mono dim">{p.client_macs.length || "—"}</td>
                <td className="mono">{p.block_list.length}</td>
                <td className="dim">{p.schedule}</td>
                <td style={{ textAlign: "right" }}>
                  <button className="btn sm" onClick={() => setEditingProfile(p)}>Edit</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div style={{ padding: "0 14px 14px" }} className="dim">
          Custom block and allow lists live here. Per-device targeting
          (<code>client_macs</code>) and schedules are supported by the API and currently
          unused on every profile; edit those in the UniFi console for now.
        </div>
      </div>

      {/* ---- DNS Shield ---- */}
      <div className="card">
        <div className="card-head">
          <h2>DNS Shield (encrypted upstream)</h2>
          <span className={`pill ${d.doh.state === "off" ? "warn" : ""}`}>
            {d.doh.state === "off" ? "off" : `on (${d.doh.state})`}
          </span>
          <div className="spacer" />
          <button className="btn sm" disabled={doh.isPending}
                  onClick={() => doh.mutate({ state: d.doh.state === "off" ? "auto" : "off" })}>
            {d.doh.state === "off" ? "Enable" : "Disable"}
          </button>
        </div>
        <div style={{ padding: 14, display: "grid", gap: 12 }}>
          <div className="dim">
            Encrypts queries between the gateway and its upstream resolver over HTTPS,
            so your ISP sees an encrypted session instead of a readable list of every
            domain you look up. It replaces whatever DNS the WAN was configured with.
            It does not filter anything by itself.
          </div>

          <div>
            <div className="dim" style={{ marginBottom: 6 }}>Providers</div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {d.doh.known_providers.map((name) => {
                const on = d.doh.server_names.includes(name);
                return (
                  <button key={name} className={`btn sm ${on ? "primary" : ""}`}
                          disabled={doh.isPending}
                          onClick={() => doh.mutate({
                            server_names: on
                              ? d.doh.server_names.filter((x) => x !== name)
                              : [...d.doh.server_names, name],
                          })}>
                    {name}
                  </button>
                );
              })}
            </div>
          </div>

          {d.doh.unverified_providers.length > 0 && (
            <div className="banner warn">
              Unrecognised provider {d.doh.unverified_providers.join(", ")}. UniFi does
              not validate this field, so a typo is stored happily and then breaks
              resolution. Only <code>{d.doh.known_providers.join("</code>, <code>")}</code>{" "}
              are confirmed working on this firmware.
            </div>
          )}

          {d.doh.state !== "off" && d.doh.server_names.length === 0 &&
           d.doh.custom_servers.length === 0 && (
            <div className="banner err">
              DNS Shield is on with no provider selected. Pick one before relying on it.
            </div>
          )}

          {d.content_filters.some((p) => p.enabled) && (
            <div className="banner warn">
              Content filtering is enabled on{" "}
              {d.content_filters.filter((p) => p.enabled).map((p) => p.name).join(", ")}.
              Ubiquiti documents that content filtering disables DNS Shield for that
              network, falling back to plaintext DNS on port 53. If that still holds on
              this firmware, those networks are not encrypted.
            </div>
          )}
        </div>
      </div>

      {/* ---- traffic logging ---- */}
      <div className="card">
        <div className="card-head">
          <h2>DNS query logging</h2>
          <span className={`pill ${d.traffic_logging.gateway_dns_enabled ? "" : "warn"}`}>
            {d.traffic_logging.gateway_dns_enabled ? "on" : "off"}
          </span>
          <div className="spacer" />
          <button className="btn sm" disabled={flow.isPending}
                  onClick={() => flow.mutate({
                    gateway_dns_enabled: !d.traffic_logging.gateway_dns_enabled })}>
            {d.traffic_logging.gateway_dns_enabled ? "Disable" : "Enable"}
          </button>
        </div>
        <div style={{ padding: 14 }} className="dim">
          Gateway DNS flow logging. Produces per-query records with the client name, IP,
          MAC, network, zone, and allow/block verdict. Required for the stats page.
        </div>
      </div>

      {editingNet && (
        <NetworkDnsModal net={editingNet} busy={netDns.isPending}
                         onCancel={() => setEditingNet(null)}
                         onSave={(enabled, servers, domain) =>
                           netDns.mutate({ id: editingNet.id, enabled, servers, domain })} />
      )}
      {editingProfile && (
        <ProfileModal p={editingProfile} allCategories={d.categories}
                      busy={contentFilter.isPending}
                      onCancel={() => setEditingProfile(null)}
                      onSave={(body) => contentFilter.mutate({ id: editingProfile.id, body })} />
      )}
    </>
  );
}

/** Mirrors the backend check so the warning appears as you type. */
function domainAdvice(d: string): { severity: "high" | "info"; detail: string } | null {
  const v = d.trim().replace(/\.$/, "").toLowerCase();
  if (!v) return null;
  if (v.endsWith(".local"))
    return { severity: "high", detail:
      "`.local` is reserved for mDNS. Apple, Avahi and Android send these lookups to multicast instead of the gateway, so records under it resolve inconsistently." };
  if (v.endsWith(".internal") || v.endsWith(".home.arpa")) return null;
  if (/\.(lan|home|corp|mail|lab|box)$/.test(v))
    return { severity: "info", detail:
      "Undelegated today but never reserved, so a future gTLD round could collide. `.internal` is reserved permanently for private use." };
  return { severity: "info", detail:
    "Not a reserved private-use suffix. If you also own this domain publicly, internal names will shadow the real ones." };
}

function NetworkDnsModal(
  { net, busy, onCancel, onSave }: {
    net: NetworkDns; busy: boolean;
    onCancel: () => void;
    onSave: (enabled: boolean, servers: string[], domain: string) => void;
  },
) {
  const [enabled, setEnabled] = useState(net.dhcpd_dns_enabled);
  const [domain, setDomain] = useState(net.domain_name ?? "");
  const [text, setText] = useState(net.servers.join(", "));
  const advice = domainAdvice(domain);
  const servers = toList(text);
  const pub = servers.filter((s) => PUBLIC_RESOLVERS.has(s));
  const mixed = pub.length > 0 && pub.length < servers.length;

  return (
    <div className="modal-back" onClick={onCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>DNS for {net.name}</h3>
        <div className="modal-body">
          <label style={{ flexDirection: "row", alignItems: "center", display: "flex", gap: 8 }}>
            <input type="checkbox" checked={!enabled} style={{ width: "auto" }}
                   onChange={(e) => setEnabled(!e.target.checked)} />
            Inherit the gateway resolver (recommended)
          </label>
          {enabled && (
            <label>
              DHCP DNS servers, in order (max 4)
              <input className="mono" value={text} onChange={(e) => setText(e.target.value)}
                     placeholder="192.168.1.1" />
            </label>
          )}
          {enabled && mixed && (
            <div className="banner err">
              Mixing a private resolver with a public one ({pub.join(", ")}) does not give
              you failover. Clients pick freely, so some queries bypass filtering and
              local records entirely.
            </div>
          )}
          {enabled && pub.length === servers.length && servers.length > 0 && (
            <div className="banner err">
              Only public resolvers listed. This network gets no local DNS records and
              no filtering.
            </div>
          )}

          <label>
            Search domain
            <input className="mono" value={domain} placeholder="example.internal"
                   onChange={(e) => setDomain(e.target.value)} />
          </label>
          <div className="dim" style={{ fontSize: 12, marginTop: -6 }}>
            Clients append this to unqualified names, so <code>plex</code> resolves as{" "}
            <code>plex.{domain || "example.internal"}</code>. Leave empty for none.
            Only affects lookups with no dot in them.
          </div>
          {advice && (
            <div className={`banner ${advice.severity === "high" ? "err" : "warn"}`}>
              {advice.detail}
            </div>
          )}
        </div>
        <div className="modal-foot">
          <button className="btn" onClick={onCancel} disabled={busy}>Cancel</button>
          <button className="btn primary" disabled={busy}
                  onClick={() => onSave(enabled, servers.slice(0, 4), domain.trim())}>
            Save
          </button>
        </div>
      </div>
    </div>
  );
}

function ProfileModal(
  { p, allCategories, busy, onCancel, onSave }: {
    p: ContentFilterView; allCategories: string[]; busy: boolean;
    onCancel: () => void; onSave: (b: Record<string, unknown>) => void;
  },
) {
  const [enabled, setEnabled] = useState(p.enabled);
  const [cats, setCats] = useState<string[]>(p.categories);
  const [block, setBlock] = useState(p.block_list.join("\n"));
  const [allow, setAllow] = useState(p.allow_list.join("\n"));
  const [catSearch, setCatSearch] = useState("");

  const shown = allCategories
    .filter((c) => !catSearch || c.toLowerCase().includes(catSearch.toLowerCase()))
    .slice(0, 60);

  return (
    <div className="modal-back" onClick={onCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>{p.name} filtering</h3>
        <div className="modal-body">
          <label style={{ flexDirection: "row", alignItems: "center", display: "flex", gap: 8 }}>
            <input type="checkbox" checked={enabled} style={{ width: "auto" }}
                   onChange={(e) => setEnabled(e.target.checked)} />
            Profile enabled
          </label>

          <label>
            Categories ({cats.length} of {allCategories.length})
            <input type="search" placeholder="Filter categories..." value={catSearch}
                   onChange={(e) => setCatSearch(e.target.value)} />
          </label>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, maxHeight: 190,
                        overflowY: "auto", padding: 2 }}>
            {shown.map((cName) => {
              const on = cats.includes(cName);
              return (
                <button key={cName} className={`btn sm ${on ? "primary" : ""}`}
                        title={cName}
                        onClick={() => setCats(on ? cats.filter((x) => x !== cName) : [...cats, cName])}>
                  {categoryLabel(cName)}
                </button>
              );
            })}
          </div>

          <label>
            Blocked domains ({toList(block).length})
            <textarea rows={5} className="mono" value={block}
                      onChange={(e) => setBlock(e.target.value)} placeholder="one per line" />
          </label>
          <label>
            Allowed domains ({toList(allow).length})
            <textarea rows={3} className="mono" value={allow}
                      onChange={(e) => setAllow(e.target.value)} />
          </label>
          <div className="dim" style={{ fontSize: 12 }}>
            Large lists hit a request payload ceiling. A few thousand domains per profile
            is the practical limit; 50k-entry public blocklists will not fit.
          </div>
        </div>
        <div className="modal-foot">
          <button className="btn" onClick={onCancel} disabled={busy}>Cancel</button>
          <button className="btn primary" disabled={busy}
                  onClick={() => onSave({
                    enabled, categories: cats,
                    block_list: toList(block), allow_list: toList(allow),
                  })}>Save</button>
        </div>
      </div>
    </div>
  );
}
