import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import type { MigrateItem, MigratePreview, RenameItem, RenamePreview } from "./types";

type Mode = "pihole" | "text";
type Tab = "import" | "rename";

/** Buckets the preview sorts records into, and whether they are safe to import. */
const BUCKETS: {
  key: "new" | "conflict" | "shadowed" | "duplicate";
  label: string;
  note: string;
  defaultOn: boolean;
}[] = [
  { key: "new", label: "New", defaultOn: true,
    note: "Not on the gateway. These are the import." },
  { key: "conflict", label: "Conflicting", defaultOn: false,
    note: "Same name and type already exist with a different value. Importing adds a second answer rather than replacing the first, which is round-robin, not an update." },
  { key: "shadowed", label: "Shadowed by a client record", defaultOn: false,
    note: "A client device already publishes this name via its Local DNS Record. Adding a DNS store record too would give the gateway two answers for it." },
  { key: "duplicate", label: "Already present", defaultOn: false,
    note: "Identical record already exists. Nothing to do." },
];

export function MigratePage() {
  const [tab, setTab] = useState<Tab>("import");
  return (
    <>
      <h1>Migrate</h1>
      <div className="sub">
        Bring records in from another resolver, or move an existing zone to a new
        domain. Nothing is written until you review the plan and confirm.
      </div>
      <div className="toolbar">
        <button className={`btn ${tab === "import" ? "primary" : ""}`}
                onClick={() => setTab("import")}>Import from resolver</button>
        <button className={`btn ${tab === "rename" ? "primary" : ""}`}
                onClick={() => setTab("rename")}>Rename a domain</button>
      </div>
      {tab === "import" ? <ImportPanel /> : <RenamePanel />}
    </>
  );
}

function ImportPanel() {
  const qc = useQueryClient();
  const [mode, setMode] = useState<Mode>("pihole");
  const [url, setUrl] = useState("http://pi.hole");
  const [password, setPassword] = useState("");
  const [token, setToken] = useState("");
  const [hostsText, setHostsText] = useState("");
  const [cnameText, setCnameText] = useState("");
  const [ttl, setTtl] = useState(300);
  const [preview, setPreview] = useState<MigratePreview | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [err, setErr] = useState<string | null>(null);
  const [applied, setApplied] = useState<string | null>(null);

  const keyOf = (r: MigrateItem) => `${r.fqdn}|${r.kind}|${r.value}`;

  const doPreview = useMutation({
    mutationFn: () =>
      api.migratePreview({
        ttl,
        source: mode === "pihole"
          ? { mode, url, password: password || null, token: token || null }
          : { mode, hosts_text: hostsText, cname_text: cnameText },
      }),
    onSuccess: (p) => {
      setPreview(p); setErr(null); setApplied(null);
      const on = new Set<string>();
      BUCKETS.filter((b) => b.defaultOn).forEach((b) =>
        p[b.key].forEach((r) => on.add(keyOf(r))));
      setPicked(on);
    },
    onError: (e: Error) => { setErr(e.message); setPreview(null); },
  });

  const doApply = useMutation({
    mutationFn: () => {
      const all = BUCKETS.flatMap((b) => preview![b.key]);
      return api.migrateApply(
        all.filter((r) => picked.has(keyOf(r))).map((r) => r.payload),
        ttl, `import from ${preview!.source}`,
      );
    },
    onSuccess: (cs) => {
      setApplied(cs.id); setErr(null); setPreview(null);
      qc.invalidateQueries({ queryKey: ["zones"] });
      qc.invalidateQueries({ queryKey: ["changesets"] });
    },
    onError: (e: Error) => setErr(e.message),
  });

  const toggle = (r: MigrateItem) => {
    const k = keyOf(r);
    setPicked((p) => {
      const n = new Set(p);
      n.has(k) ? n.delete(k) : n.add(k);
      return n;
    });
  };

  return (
    <>
      {err && <div className="banner err">{err}</div>}
      {applied && (
        <div className="banner ok">
          Imported as one changeset. It appears under History and rolls back as a unit.
        </div>
      )}

      <div className="card">
        <div className="card-head"><h2>Source</h2></div>
        <div style={{ padding: 14, display: "grid", gap: 13 }}>
          <div style={{ display: "flex", gap: 8 }}>
            <button className={`btn ${mode === "pihole" ? "primary" : ""}`}
                    onClick={() => setMode("pihole")}>Pi-hole API</button>
            <button className={`btn ${mode === "text" ? "primary" : ""}`}
                    onClick={() => setMode("text")}>Paste files</button>
          </div>

          {mode === "pihole" ? (
            <>
              <label>
                Pi-hole address
                <input className="mono" value={url} onChange={(e) => setUrl(e.target.value)}
                       placeholder="http://pi.hole" />
              </label>
              <div className="row2">
                <label>
                  Web password (Pi-hole v6)
                  <input type="password" value={password}
                         onChange={(e) => setPassword(e.target.value)} />
                </label>
                <label>
                  API token (Pi-hole v5)
                  <input type="password" value={token}
                         onChange={(e) => setToken(e.target.value)}
                         placeholder="Settings &gt; API &gt; Show token" />
                </label>
              </div>
              <div className="dim" style={{ fontSize: 12 }}>
                v6 is tried first with the password, then v5 with the token. Supply
                whichever you have. The credential is used for this request only and is
                never stored.
              </div>
            </>
          ) : (
            <>
              <label>
                <code>/etc/pihole/custom.list</code> (hosts format)
                <textarea rows={7} className="mono" value={hostsText}
                          onChange={(e) => setHostsText(e.target.value)}
                          placeholder="192.0.2.10 nas.example.internal" />
              </label>
              <label>
                <code>/etc/dnsmasq.d/05-pihole-custom-cname.conf</code>
                <textarea rows={4} className="mono" value={cnameText}
                          onChange={(e) => setCnameText(e.target.value)}
                          placeholder="cname=git.example.internal,nas.example.internal" />
              </label>
              <div className="dim" style={{ fontSize: 12 }}>
                Works when the Pi-hole is already off, or its password is long gone.
              </div>
            </>
          )}

          <div style={{ display: "flex", gap: 10, alignItems: "flex-end" }}>
            <label style={{ maxWidth: 170 }}>
              TTL for imported records
              <input type="number" min={0} value={ttl}
                     onChange={(e) => setTtl(Number(e.target.value))} />
            </label>
            <button className="btn primary" disabled={doPreview.isPending}
                    onClick={() => doPreview.mutate()}>
              {doPreview.isPending ? "Reading..." : "Preview import"}
            </button>
          </div>
        </div>
      </div>

      {preview && (
        <>
          <div className="banner">
            Read <strong>{preview.counts.imported}</strong> records from{" "}
            <strong>{preview.source}</strong>: {preview.counts.new} new,{" "}
            {preview.counts.duplicate} already present, {preview.counts.conflict} conflicting,{" "}
            {preview.counts.shadowed} shadowed by client records, {preview.counts.skipped} unreadable.
          </div>

          {BUCKETS.map((b) => preview[b.key].length > 0 && (
            <div className="card" key={b.key}>
              <div className="card-head">
                <h2>{b.label}</h2>
                <span className={`pill ${b.key === "new" ? "" : "warn"}`}>
                  {preview[b.key].length}
                </span>
                <div className="spacer" />
                <button className="btn sm" onClick={() => {
                  const keys = preview[b.key].map(keyOf);
                  const allOn = keys.every((k) => picked.has(k));
                  setPicked((p) => {
                    const n = new Set(p);
                    keys.forEach((k) => (allOn ? n.delete(k) : n.add(k)));
                    return n;
                  });
                }}>Toggle all</button>
              </div>
              <table>
                <thead><tr>
                  <th style={{ width: 34 }} /><th>Name</th>
                  <th style={{ width: 80 }}>Type</th><th>Value</th>
                  {b.key === "conflict" && <th>Already on gateway</th>}
                </tr></thead>
                <tbody>
                  {preview[b.key].map((r) => (
                    <tr key={keyOf(r)}>
                      <td>
                        <input type="checkbox" style={{ width: "auto" }}
                               checked={picked.has(keyOf(r))} onChange={() => toggle(r)} />
                      </td>
                      <td className="mono">{r.fqdn}</td>
                      <td><span className="tag">{r.kind}</span></td>
                      <td className="mono">{r.value}</td>
                      {b.key === "conflict" && (
                        <td className="mono dim">{(r.existing ?? []).join(", ")}</td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
              <div style={{ padding: "0 14px 14px" }} className="dim">{b.note}</div>
            </div>
          ))}

          {preview.skipped.length > 0 && (
            <div className="card">
              <div className="card-head">
                <h2>Could not read</h2><span className="pill warn">{preview.skipped.length}</span>
              </div>
              <table><tbody>
                {preview.skipped.map((s, i) => (
                  <tr key={i}>
                    <td className="dim mono" style={{ width: 60 }}>{s.line ?? ""}</td>
                    <td className="mono">{s.text}</td>
                    <td className="dim">{s.why}</td>
                  </tr>
                ))}
              </tbody></table>
            </div>
          )}

          {picked.size > 10 && (
            <div className="banner warn">
              Importing {picked.size} records writes them one at a time, and the gateway
              reloads its resolver on every change. Each reload is roughly a second of
              DNS unavailability, so expect intermittent resolution for about{" "}
              {Math.ceil(picked.size * 1.5)} seconds. Worth doing at a quiet moment.
            </div>
          )}
          <div className="toolbar">
            <div className="spacer" />
            <span className="dim">{picked.size} selected</span>
            <button className="btn primary" disabled={doApply.isPending || picked.size === 0}
                    onClick={() => doApply.mutate()}>
              {doApply.isPending ? "Importing..." : `Import ${picked.size} records`}
            </button>
          </div>
        </>
      )}
    </>
  );
}


/* ------------------------------------------------------------- rename ---- */

function RenamePanel() {
  const qc = useQueryClient();
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [preview, setPreview] = useState<RenamePreview | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const doPreview = useMutation({
    mutationFn: () => api.renamePreview(from.trim(), to.trim()),
    onSuccess: (p) => {
      setPreview(p); setErr(null); setDone(null);
      setPicked(new Set(p.plan.map((r) => r.old_fqdn + r.type)));
    },
    onError: (e: Error) => { setErr(e.message); setPreview(null); },
  });

  const doApply = useMutation({
    mutationFn: () =>
      api.renameApply(
        preview!.plan.filter((r) => picked.has(r.old_fqdn + r.type)).map((r) => r.payload),
        `rename ${preview!.from_apex} -> ${preview!.to_apex}`,
      ),
    onSuccess: () => {
      setDone("added"); setErr(null);
      qc.invalidateQueries({ queryKey: ["zones"] });
      qc.invalidateQueries({ queryKey: ["changesets"] });
      doPreview.mutate();
    },
    onError: (e: Error) => setErr(e.message),
  });

  const doRemoveOld = useMutation({
    mutationFn: () =>
      api.removeRecords(
        preview!.already_exists.map((r) => r.old_id!).filter(Boolean),
        `remove old ${preview!.from_apex} records after rename`,
      ),
    onSuccess: () => {
      setDone("removed"); setErr(null);
      qc.invalidateQueries({ queryKey: ["zones"] });
      doPreview.mutate();
    },
    onError: (e: Error) => setErr(e.message),
  });

  const toggle = (r: RenameItem) => setPicked((p) => {
    const n = new Set(p); const k = r.old_fqdn + r.type;
    n.has(k) ? n.delete(k) : n.add(k);
    return n;
  });

  return (
    <>
      {err && <div className="banner err">{err}</div>}
      {done === "added" && (
        <div className="banner ok">
          New names created. Both domains resolve now. Repoint your clients and
          services, then use <strong>Remove old records</strong> below.
        </div>
      )}
      {done === "removed" && <div className="banner ok">Old records removed.</div>}

      <div className="card">
        <div className="card-head"><h2>Move a zone to a new domain</h2></div>
        <div style={{ padding: 14, display: "grid", gap: 13 }}>
          <div className="row2">
            <label>From apex
              <input className="mono" value={from} onChange={(e) => setFrom(e.target.value)}
                     placeholder="old.example" />
            </label>
            <label>To apex
              <input className="mono" value={to} onChange={(e) => setTo(e.target.value)}
                     placeholder="example.internal" />
            </label>
          </div>
          <div className="dim" style={{ fontSize: 12 }}>
            New records are <strong>added</strong>, not renamed in place, so both names
            resolve while you repoint things. Removing the originals is a separate step.
            CNAME targets inside the old apex follow it across automatically.
          </div>
          <div>
            <button className="btn primary" disabled={!from.trim() || !to.trim() || doPreview.isPending}
                    onClick={() => doPreview.mutate()}>
              {doPreview.isPending ? "Planning..." : "Plan the move"}
            </button>
          </div>
        </div>
      </div>

      {preview && (
        <>
          <div className="banner">
            <strong>{preview.counts.move}</strong> records to add under{" "}
            <code>{preview.to_apex}</code>
            {preview.counts.already > 0 && <> · <strong>{preview.counts.already}</strong> already moved</>}
            {preview.counts.client_bound > 0 && <> · <strong>{preview.counts.client_bound}</strong> live on a client device</>}
          </div>

          {preview.plan.length > 0 && (
            <div className="card">
              <div className="card-head"><h2>Will be added</h2>
                <span className="pill">{preview.plan.length}</span></div>
              <table>
                <thead><tr>
                  <th style={{ width: 34 }} /><th>From</th><th>To</th>
                  <th style={{ width: 80 }}>Type</th><th>Value</th>
                </tr></thead>
                <tbody>
                  {preview.plan.map((r) => (
                    <tr key={r.old_fqdn + r.type}>
                      <td><input type="checkbox" style={{ width: "auto" }}
                                 checked={picked.has(r.old_fqdn + r.type)}
                                 onChange={() => toggle(r)} /></td>
                      <td className="mono dim">{r.old_fqdn}</td>
                      <td className="mono">{r.new_fqdn}</td>
                      <td><span className="tag">{r.type.replace("_RECORD", "")}</span></td>
                      <td className="mono dim">{r.value}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {picked.size > 10 && (
                <div style={{ padding: "0 14px 14px" }}>
                  <div className="banner warn" style={{ marginBottom: 0 }}>
                    {picked.size} writes, and the gateway reloads its resolver on each
                    one. Expect intermittent DNS for roughly {Math.ceil(picked.size * 1.5)}s.
                  </div>
                </div>
              )}
              <div style={{ padding: "0 14px 14px", display: "flex", gap: 8 }}>
                <div className="spacer" />
                <button className="btn primary" disabled={doApply.isPending || picked.size === 0}
                        onClick={() => doApply.mutate()}>
                  {doApply.isPending ? "Adding..." : `Add ${picked.size} records`}
                </button>
              </div>
            </div>
          )}

          {preview.client_bound.length > 0 && (
            <div className="card">
              <div className="card-head"><h2>Living on a client device</h2>
                <span className="pill warn">{preview.client_bound.length}</span></div>
              <table><tbody>
                {preview.client_bound.map((c) => (
                  <tr key={c.fqdn}>
                    <td className="mono">{c.fqdn}</td>
                    <td className="dim">on {c.client}</td>
                    <td className="mono dim">suggest {c.suggested}</td>
                  </tr>
                ))}
              </tbody></table>
              <div style={{ padding: "0 14px 14px" }} className="dim">
                These are Local DNS Records on the device itself, not in the DNS store.
                Rename them from the DNS Records page so the change is deliberate.
              </div>
            </div>
          )}

          {preview.already_exists.length > 0 && (
            <div className="card">
              <div className="card-head"><h2>Already moved</h2>
                <span className="pill ok">{preview.already_exists.length}</span>
                <div className="spacer" />
                <button className="btn sm danger" disabled={doRemoveOld.isPending}
                        onClick={() => {
                          if (confirm(`Delete ${preview.already_exists.length} old ${preview.from_apex} records? Only do this once everything resolves on the new name.`))
                            doRemoveOld.mutate();
                        }}>
                  Remove old records
                </button>
              </div>
              <table><tbody>
                {preview.already_exists.map((r) => (
                  <tr key={r.old_fqdn + r.type}>
                    <td className="mono dim">{r.old_fqdn}</td>
                    <td className="mono">{r.new_fqdn}</td>
                    <td className="mono dim">{r.value}</td>
                  </tr>
                ))}
              </tbody></table>
              <div style={{ padding: "0 14px 14px" }} className="dim">
                The new name exists for each of these. Removing the originals is safe
                once clients, bookmarks, reverse proxies and certificates all use the
                new domain.
              </div>
            </div>
          )}
        </>
      )}
    </>
  );
}
