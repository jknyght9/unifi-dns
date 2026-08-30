import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import { RecordEditor } from "./RecordEditor";
import { RECORD_TYPES, shortType } from "./types";
import { IconCaret } from "./icons";
import type { ChangeSet, RecordType, RenderedRecord } from "./types";

/* ------------------------------------------------------------------ Records */

export function RecordsPage() {
  const qc = useQueryClient();
  const zones = useQuery({ queryKey: ["zones"], queryFn: api.zones });
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState<RecordType | "">("");
  const [editing, setEditing] = useState<RenderedRecord | null | undefined>(undefined);
  const [err, setErr] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(() => {
    try { return new Set(JSON.parse(localStorage.getItem("zones.collapsed") ?? "[]")); }
    catch { return new Set(); }
  });
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const toggleZone = (key: string) => setCollapsed((c) => {
    const n = new Set(c);
    n.has(key) ? n.delete(key) : n.add(key);
    localStorage.setItem("zones.collapsed", JSON.stringify([...n]));
    return n;
  });
  const toggleSel = (id: string) => setSelected((s2) => {
    const n = new Set(s2); n.has(id) ? n.delete(id) : n.add(id); return n;
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["zones"] });
    qc.invalidateQueries({ queryKey: ["changesets"] });
  };
  const create = useMutation({
    mutationFn: (p: Record<string, unknown>) => api.createRecord(p),
    onSuccess: () => { setEditing(undefined); setErr(null); invalidate(); },
    onError: (e: Error) => setErr(e.message),
  });
  const update = useMutation({
    mutationFn: ({ id, p }: { id: string; p: Record<string, unknown> }) => api.updateRecord(id, p),
    onSuccess: () => { setEditing(undefined); setErr(null); invalidate(); },
    onError: (e: Error) => setErr(e.message),
  });
  const del = useMutation({
    mutationFn: (id: string) => api.deleteRecord(id),
    onSuccess: invalidate,
    onError: (e: Error) => setErr(e.message),
  });
  // Bulk delete goes through the changeset endpoint so a purge is one entry in
  // History and rolls back as a unit, rather than N separate deletions.
  const bulkDel = useMutation({
    mutationFn: (ids: string[]) => api.removeRecords(ids, `delete ${ids.length} records`),
    onSuccess: () => { setSelected(new Set()); setErr(null); invalidate(); },
    onError: (e: Error) => setErr(e.message),
  });
  const clientRec = useMutation({
    mutationFn: ({ id, hostname, enabled }:
      { id: string; hostname: string | null; enabled?: boolean }) =>
      api.setClientRecord(id, hostname, enabled),
    onSuccess: invalidate,
    onError: (e: Error) => setErr(e.message),
  });

  const filtered = useMemo(() => {
    if (!zones.data) return [];
    const q = search.trim().toLowerCase();
    return zones.data.zones
      .map((z) => ({
        ...z,
        records: z.records.filter(
          (r) =>
            (!typeFilter || r.type === typeFilter) &&
            (!q || r.fqdn.toLowerCase().includes(q) || r.value.toLowerCase().includes(q)),
        ),
      }))
      .filter((z) => z.records.length > 0 || z.ungrouped);
  }, [zones.data, search, typeFilter]);

  const shown = filtered.reduce((n, z) => n + z.records.length, 0);

  return (
    <>
      <h1>DNS Records</h1>
      <div className="sub">
        {zones.data
          ? `${zones.data.total} records across ${zones.data.zones.length} zones` +
            ` · ${zones.data.policy_count} in the DNS store, ${zones.data.client_count} bound to clients`
          : "Loading..."}
        {shown !== zones.data?.total && zones.data ? ` · ${shown} matching` : ""}
      </div>

      {err && <div className="banner err">{err}</div>}
      {zones.isError && <div className="banner err">{(zones.error as Error).message}</div>}

      {/* UniFi keeps local DNS records in two unrelated places and the native UI
          never says so, which makes the distinction baffling on first contact. */}
      {(zones.data?.client_count ?? 0) > 0 && (
        <div className="banner">
          <strong>Stored in</strong> is where UniFi keeps the record.{" "}
          <span className="tag">DNS store</span> records are free-standing, created under
          Settings &gt; Routing &gt; DNS, and can be edited or deleted freely.{" "}
          <span className="tag">Client</span> records are the Local DNS Record field on a
          specific device, so they can be renamed, disabled, or cleared, but not deleted
          on their own. UniFi's DNS API does not report them at all.
        </div>
      )}

      <div className="toolbar">
        <input type="search" placeholder="Search name or value..." value={search}
               onChange={(e) => setSearch(e.target.value)} />
        <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value as RecordType | "")}>
          <option value="">All types</option>
          {RECORD_TYPES.map((t) => <option key={t} value={t}>{shortType(t)}</option>)}
        </select>
        <div className="spacer" />
        <button className="btn" onClick={() => {
          const keys = (zones.data?.zones ?? []).map((z) => z.apex);
          setCollapsed((c) => {
            const n = c.size >= keys.length ? new Set<string>() : new Set(keys);
            localStorage.setItem("zones.collapsed", JSON.stringify([...n]));
            return n;
          });
        }}>
          {collapsed.size >= (zones.data?.zones.length ?? 0) ? "Expand all" : "Collapse all"}
        </button>
        <button className="btn primary" onClick={() => setEditing(null)}>New record</button>
      </div>

      {zones.data && filtered.length === 0 && (
        <div className="card"><div className="empty">
          {zones.data.total === 0
            ? "No records on the gateway yet. Create one, or import from your existing resolver."
            : "Nothing matches that filter."}
        </div></div>
      )}

      {filtered.map((z) => {
        const open = !collapsed.has(z.apex);
        const deletable = z.records.filter((r) => r.source === "policy" && r.id).map((r) => r.id!);
        const allSel = deletable.length > 0 && deletable.every((id) => selected.has(id));
        return (
        <div className="card" key={z.apex}>
          <div className="card-head clickable" onClick={() => toggleZone(z.apex)}>
            <IconCaret open={open} />
            <h2>{z.bare ? "Bare hostnames" : z.ungrouped ? "Ungrouped" : z.apex}</h2>
            <span className="pill">{z.records.length}</span>
            {open && z.ungrouped && z.count === 0 && (
            <div className="empty">
              Nothing unmatched. Every record sits under a declared apex or is a bare
              hostname. Records land here when their name matches none of your apex
              domains, which usually means an apex is missing rather than the record
              being wrong.
            </div>
          )}
          {open && z.bare && (
              <span className="pill" title="Single-label names with no domain part">
                no domain
              </span>
            )}
            {z.ungrouped && z.count > 0 && (
              <span className="pill warn">no matching apex declared</span>
            )}
            <div className="spacer" />
            {deletable.length > 0 && (
              <button className="btn sm" onClick={(e) => {
                e.stopPropagation();
                setSelected((s2) => {
                  const n = new Set(s2);
                  deletable.forEach((id) => (allSel ? n.delete(id) : n.add(id)));
                  return n;
                });
              }}>{allSel ? "Deselect all" : "Select all"}</button>
            )}
          </div>
          {open && z.ungrouped && z.count === 0 && (
            <div className="empty">
              Nothing unmatched. Every record sits under a declared apex or is a bare
              hostname. Records land here when their name matches none of your apex
              domains, which usually means an apex is missing rather than the record
              being wrong.
            </div>
          )}
          {open && z.bare && (
            <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)" }}
                 className="dim">
              Names with no domain part. These resolve on every VLAN regardless of the
              DHCP search domain, which is why they are worth keeping even when the
              fully qualified record already exists. A CNAME to the FQDN keeps the
              address in one place; duplicate A records drift apart.
            </div>
          )}
          {open && z.count > 0 && (
          <table>
            <thead>
              <tr>
                <th style={{ width: 30 }} />
                <th style={{ width: "26%" }}>Name</th>
                <th style={{ width: 150 }}>Stored in</th>
                <th style={{ width: 80 }}>Type</th>
                <th>Value</th>
                <th style={{ width: 70 }}>TTL</th>
                <th style={{ width: 152 }} />
              </tr>
            </thead>
            <tbody>
              {z.records.map((r) => (
                <tr key={r.id ?? r.fqdn + r.value} className={r.enabled ? "" : "disabled-row"}>
                  <td>
                    {r.source === "policy" && r.id && (
                      <input type="checkbox" style={{ width: "auto" }}
                             checked={selected.has(r.id)}
                             onChange={() => toggleSel(r.id!)} />
                    )}
                  </td>
                  <td className="mono">
                    {z.ungrouped || z.bare ? r.fqdn : r.label}
                    {!r.enabled && <span className="tag" style={{ marginLeft: 8 }}>disabled</span>}
                  </td>
                  <td title={r.source === "client"
                        ? "Set on the client device itself (Local DNS Record). Invisible to UniFi's DNS API, but the gateway resolves it."
                        : "A free-standing record in UniFi's DNS store (Settings > Routing > DNS)."}>
                    <span className="tag">{r.source === "client" ? "Client" : "DNS store"}</span>
                    {r.source === "client" && r.client_name && (
                      <div className="dim" style={{ fontSize: 11.5, marginTop: 2 }}>
                        {r.client_name}
                      </div>
                    )}
                  </td>
                  <td><span className="tag">{shortType(r.type)}</span></td>
                  <td className="mono">
                    {r.value}
                    {r.unstable && (
                      <span className="tag warn" style={{ marginLeft: 8 }}
                            title="This client has no DHCP reservation, so the address can change and the record will point at the wrong host.">
                        no fixed IP
                      </span>
                    )}
                  </td>
                  <td className="dim mono" title={r.ttl_seconds ? undefined : "Gateway default (ttlSeconds 0)"}>
                    {r.ttl_seconds ?? "auto"}
                  </td>
                  <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                    {r.source === "policy" ? (
                      <>
                        <button className="btn sm" onClick={() => setEditing(r)}>Edit</button>{" "}
                        <button className="btn sm danger"
                                onClick={() => { if (r.id && confirm(`Delete ${r.fqdn}?`)) del.mutate(r.id); }}>
                          Delete
                        </button>
                      </>
                    ) : (
                      <>
                        <button className="btn sm" disabled={clientRec.isPending}
                                onClick={() => r.id && clientRec.mutate(
                                  { id: r.id, hostname: r.fqdn, enabled: !r.enabled })}>
                          {r.enabled ? "Disable" : "Enable"}
                        </button>{" "}
                        <button className="btn sm danger" disabled={clientRec.isPending}
                                onClick={() => {
                                  if (r.id && confirm(`Clear the local DNS record on ${r.client_name}?`))
                                    clientRec.mutate({ id: r.id, hostname: null });
                                }}>
                          Clear
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          )}
        </div>
        );
      })}

      {selected.size > 0 && (
        <div className="bulkbar">
          <strong>{selected.size}</strong> selected
          <span className="dim" style={{ fontSize: 12 }}>
            {selected.size} writes, roughly {Math.ceil(selected.size * 1.5)}s of
            intermittent DNS while the gateway reloads
          </span>
          <div className="spacer" />
          <button className="btn sm" onClick={() => setSelected(new Set())}>Clear</button>
          <button className="btn sm danger" disabled={bulkDel.isPending}
                  onClick={() => {
                    if (confirm(`Delete ${selected.size} records? This is one changeset and can be rolled back from History.`))
                      bulkDel.mutate([...selected]);
                  }}>
            {bulkDel.isPending ? "Deleting..." : `Delete ${selected.size}`}
          </button>
        </div>
      )}

      {editing !== undefined && (
        <RecordEditor
          existing={editing}
          error={err}
          busy={create.isPending || update.isPending}
          onCancel={() => { setEditing(undefined); setErr(null); }}
          onSave={(p) =>
            editing?.id ? update.mutate({ id: editing.id, p }) : create.mutate(p)}
        />
      )}
    </>
  );
}

/* ------------------------------------------------------------------ History */

const STATUS_CLASS: Record<string, string> = {
  applied: "ok", failed: "err", partial: "warn", pending: "",
};

export function HistoryPage() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["changesets"], queryFn: api.changesets });
  const [open, setOpen] = useState<string | null>(null);
  const [plan, setPlan] = useState<{ id: string; plan: unknown[] } | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const detail = useQuery({
    queryKey: ["changeset", open], queryFn: () => api.changeset(open!), enabled: !!open,
  });
  const roll = useMutation({
    mutationFn: ({ id, dry }: { id: string; dry: boolean }) => api.rollback(id, dry),
    onSuccess: (res, vars) => {
      if (vars.dry) setPlan({ id: vars.id, plan: (res.plan ?? []) as unknown[] });
      else {
        setPlan(null);
        qc.invalidateQueries({ queryKey: ["changesets"] });
        qc.invalidateQueries({ queryKey: ["zones"] });
      }
    },
    onError: (e: Error) => setErr(e.message),
  });

  return (
    <>
      <h1>History</h1>
      <div className="sub">
        Every change is a changeset, including ones that only affect what this app
        tracks. Rolling one back applies its inverse forward as a new changeset;
        history is never rewritten.
      </div>
      {err && <div className="banner err">{err}</div>}

      <div className="card">
        <table>
          <thead>
            <tr>
              <th>When</th><th>Summary</th><th style={{ width: 90 }}>Status</th>
              <th style={{ width: 90 }}>Source</th><th>Author</th><th style={{ width: 170 }} />
            </tr>
          </thead>
          <tbody>
            {(list.data ?? []).map((cs: ChangeSet) => (
              <tr key={cs.id}>
                <td className="dim mono">{new Date(cs.created_at).toLocaleString()}</td>
                <td>
                  {cs.summary}
                  {cs.reverts_id && <span className="tag" style={{ marginLeft: 8 }}>revert</span>}
                  {cs.revision_count === 0 && (
                    <span className="tag" style={{ marginLeft: 8 }}
                          title="Changed what this app tracks or how it groups records. No DNS record was modified, so there is nothing to roll back.">
                      no record change
                    </span>
                  )}
                </td>
                <td><span className={`tag ${STATUS_CLASS[cs.status]}`}>{cs.status}</span></td>
                <td className="dim">{cs.source}</td>
                <td className="dim">{cs.author.name ?? cs.author.unifi_admin ?? "—"}</td>
                <td style={{ textAlign: "right" }}>
                  <button className="btn sm" onClick={() => setOpen(cs.id)}>Details</button>{" "}
                  {cs.revision_count > 0 &&
                   (cs.status === "applied" || cs.status === "partial") && (
                    <button className="btn sm" onClick={() => roll.mutate({ id: cs.id, dry: true })}>
                      Roll back
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {list.data?.length === 0 && (
          <div className="empty">
            Nothing recorded yet. Every change made through this app appears here,
            including adopting a baseline and declaring apex domains.
          </div>
        )}
      </div>

      {plan && (
        <div className="modal-back" onClick={() => setPlan(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>Rollback plan</h3>
            <div className="modal-body">
              <div className="dim">
                These operations will be applied to the gateway as a new changeset.
              </div>
              <pre className="diff">{JSON.stringify(plan.plan, null, 2)}</pre>
            </div>
            <div className="modal-foot">
              <button className="btn" onClick={() => setPlan(null)}>Cancel</button>
              <button className="btn primary" disabled={roll.isPending}
                      onClick={() => roll.mutate({ id: plan.id, dry: false })}>
                Apply rollback
              </button>
            </div>
          </div>
        </div>
      )}

      {open && (
        <div className="modal-back" onClick={() => setOpen(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>Changeset detail</h3>
            <div className="modal-body">
              {detail.data?.error && <div className="banner err">{detail.data.error}</div>}
              {(detail.data?.revisions ?? []).map((r) => (
                <div key={r.seq}>
                  <div className="mono" style={{ marginBottom: 6 }}>
                    <span className="tag">{r.op}</span>{" "}
                    <span className="tag">{shortType(r.type)}</span> {r.fqdn}{" "}
                    {!r.applied && <span className="tag warn">not applied</span>}
                  </div>
                  {r.error && <div className="banner err">{r.error}</div>}
                  <pre className="diff">
{r.before ? `- ${JSON.stringify(r.before)}` : ""}
{r.after ? `+ ${JSON.stringify(r.after)}` : ""}
                  </pre>
                </div>
              ))}
            </div>
            <div className="modal-foot">
              <button className="btn" onClick={() => setOpen(null)}>Close</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

/* -------------------------------------------------------------------- Drift */

export function DriftPage() {
  const qc = useQueryClient();
  const drift = useQuery({ queryKey: ["drift"], queryFn: api.drift });
  const adopt = useMutation({
    mutationFn: api.adopt,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["drift"] });
      qc.invalidateQueries({ queryKey: ["zones"] });
    },
  });

  const d = drift.data;
  return (
    <>
      <h1>Drift</h1>
      <div className="sub">
        Differences between the gateway and this app's mirror. Anyone editing records
        in the native UniFi console shows up here.
      </div>
      {drift.isLoading && <div className="card"><div className="empty">Checking...</div></div>}
      {d?.clean && <div className="banner ok">In sync. No drift detected.</div>}

      {d && d.first_run && !d.clean && (
        <div className="banner">
          <strong>Not tracking yet.</strong> This is a new install, so the{" "}
          {d.only_on_gateway.length} records below are listed only because the app has
          not recorded them yet. Nothing has drifted. Start tracking to take a baseline,
          after which this page shows genuine differences.
        </div>
      )}

      {d && !d.clean && (
        <>
          <div className="toolbar">
            <div className="spacer" />
            <button className="btn primary" disabled={adopt.isPending} onClick={() => adopt.mutate()}>
              {d.first_run ? "Start tracking" : "Adopt gateway state"}
            </button>
          </div>
          {d.only_on_gateway.length > 0 && (
            <div className="card">
              <div className="card-head"><h2>{d.first_run ? "Untracked records" : "Only on gateway"}</h2>
                <span className="pill">{d.only_on_gateway.length}</span></div>
              <table><tbody>
                {d.only_on_gateway.map((r) => (
                  <tr key={r.id}><td className="mono">{r.fqdn}</td>
                    <td><span className="tag">{shortType(r.type)}</span></td>
                    <td className="mono">{r.value}</td></tr>
                ))}
              </tbody></table>
            </div>
          )}
          {d.only_in_mirror.length > 0 && (
            <div className="card">
              <div className="card-head"><h2>Only in mirror</h2>
                <span className="pill">{d.only_in_mirror.length}</span></div>
              <table><tbody>
                {d.only_in_mirror.map((r) => (
                  <tr key={r.unifi_id}><td className="mono">{r.fqdn}</td>
                    <td><span className="tag">{r.type.replace("_RECORD", "")}</span></td>
                    <td className="mono">{r.value}</td></tr>
                ))}
              </tbody></table>
            </div>
          )}
          {d.modified.length > 0 && (
            <div className="card">
              <div className="card-head"><h2>Modified</h2>
                <span className="pill">{d.modified.length}</span></div>
              <div style={{ padding: 14, display: "grid", gap: 12 }}>
                {d.modified.map((m) => (
                  <div key={m.unifi_id}>
                    <div className="mono" style={{ marginBottom: 6 }}>{m.fqdn}</div>
                    <pre className="diff">
{`- mirror:  ${JSON.stringify(m.mirror)}`}
{`
+ gateway: ${JSON.stringify(m.gateway)}`}
                    </pre>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </>
  );
}

/* ----------------------------------------------------------------- Settings */

export function SettingsPage() {
  const qc = useQueryClient();
  const apexes = useQuery({ queryKey: ["apexes"], queryFn: api.apexes });
  const suggest = useQuery({ queryKey: ["suggest"], queryFn: api.suggestApexes });
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["apexes"] });
    qc.invalidateQueries({ queryKey: ["zones"] });
  };
  const add = useMutation({
    mutationFn: (n: string) => api.addApex(n),
    onSuccess: () => { setName(""); setErr(null); refresh(); },
    onError: (e: Error) => setErr(e.message),
  });
  const remove = useMutation({ mutationFn: api.removeApex, onSuccess: refresh });

  const known = new Set((apexes.data ?? []).map((a) => a.name));
  const unused = (suggest.data?.suggestions ?? []).filter((s) => !known.has(s));

  return (
    <>
      <h1>Apex domains</h1>
      <div className="sub">
        UniFi stores records flat with no zone concept. These declared apexes are what
        this app groups by; anything that matches none appears as Ungrouped.
      </div>
      {err && <div className="banner err">{err}</div>}

      <div className="toolbar">
        <input placeholder="example.com" value={name} className="mono"
               onChange={(e) => setName(e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter" && name.trim()) add.mutate(name.trim()); }} />
        <button className="btn primary" disabled={!name.trim() || add.isPending}
                onClick={() => add.mutate(name.trim())}>Add</button>
      </div>

      <div className="card">
        <table>
          <thead><tr><th>Apex</th><th style={{ width: 90 }} /></tr></thead>
          <tbody>
            {(apexes.data ?? []).map((a) => (
              <tr key={a.id}>
                <td className="mono">{a.name}</td>
                <td style={{ textAlign: "right" }}>
                  <button className="btn sm danger" onClick={() => remove.mutate(a.id)}>Remove</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {apexes.data?.length === 0 && (
          <div className="empty">No apexes declared. Every record will show as Ungrouped.</div>
        )}
      </div>

      {unused.length > 0 && (
        <div className="card">
          <div className="card-head"><h2>Suggested from existing records</h2></div>
          <div style={{ padding: 14, display: "flex", gap: 8, flexWrap: "wrap" }}>
            {unused.map((s) => (
              <button key={s} className="btn sm mono" onClick={() => add.mutate(s)}>+ {s}</button>
            ))}
          </div>
          <div style={{ padding: "0 14px 14px" }} className="dim">
            Inferred from the last two labels, which is wrong for multi-label suffixes
            like <code>co.uk</code>. Confirm before adding.
          </div>
        </div>
      )}
    </>
  );
}
