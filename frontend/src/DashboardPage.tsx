import { useState } from "react";
import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import type { StatsClient, StatsDomain, TimelineBucket } from "./types";

const WINDOWS = [
  { h: 24, label: "24 h" },
  { h: 72, label: "3 d" },
  { h: 168, label: "7 d" },
  { h: 720, label: "30 d" },
];

const n = (v: number) => v.toLocaleString();

function Bars({ rows, danger }: { rows: { k: string; v: number }[]; danger?: boolean }) {
  const max = Math.max(1, ...rows.map((r) => r.v));
  return (
    <table>
      <tbody>
        {rows.map((r) => (
          <tr key={r.k}>
            <td className="mono" style={{ width: "44%" }}>{r.k}</td>
            <td>
              <div className="bar-row">
                <div className="bar-track">
                  <div className={`bar-fill ${danger ? "danger" : ""}`}
                       style={{ width: `${(r.v / max) * 100}%` }} />
                </div>
                <span className="mono dim" style={{ minWidth: 52, textAlign: "right" }}>
                  {n(r.v)}
                </span>
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function DashboardPage() {
  const qc = useQueryClient();
  const [hours, setHours] = useState(168);

  const [summary, blockedDomains, allowedDomains, clients, timeline, bypass] = useQueries({
    queries: [
      { queryKey: ["st.sum", hours], queryFn: () => api.statsSummary(hours) },
      { queryKey: ["st.dom.b", hours], queryFn: () => api.statsDomains(hours, true) },
      { queryKey: ["st.dom.a", hours], queryFn: () => api.statsDomains(hours, false) },
      { queryKey: ["st.cli", hours], queryFn: () => api.statsClients(hours) },
      { queryKey: ["st.tl", hours], queryFn: () => api.statsTimeline(hours) },
      { queryKey: ["st.by", hours], queryFn: () => api.statsBypass(hours) },
    ],
  });

  const collect = useMutation({
    mutationFn: api.statsCollect,
    onSuccess: () => {
      for (const k of ["st.sum", "st.dom.b", "st.dom.a", "st.cli", "st.tl", "st.by"]) {
        qc.invalidateQueries({ queryKey: [k] });
      }
    },
  });

  const s = summary.data;
  const buckets: TimelineBucket[] = timeline.data?.buckets ?? [];
  const peak = Math.max(1, ...buckets.map((b) => b.events));

  return (
    <>
      <h1>Gateway DNS</h1>
      <div className="sub">
        What the gateway filtered, and who asked. Collected from UniFi flow
        telemetry.
      </div>

      <div className="toolbar">
        {WINDOWS.map((w) => (
          <button key={w.h} className={`btn sm ${hours === w.h ? "primary" : ""}`}
                  onClick={() => setHours(w.h)}>{w.label}</button>
        ))}
        <div className="spacer" />
        {s?.coverage.oldest && (
          <span className="dim" style={{ fontSize: 12 }}>
            data since {new Date(s.coverage.oldest).toLocaleString()}
          </span>
        )}
        <button className="btn sm" disabled={collect.isPending}
                onClick={() => collect.mutate()}>
          {collect.isPending ? "Collecting..." : "Collect now"}
        </button>
      </div>

      <div className="tiles">
        <div className="tile">
          <div className="label">Queries blocked</div>
          <div className="value danger">{s ? n(s.dns_blocked) : "-"}</div>
          <div className="foot">{s ? `${s.dns_block_rate}% of DNS traffic seen` : ""}</div>
        </div>
        <div className="tile">
          <div className="label">DNS seen</div>
          <div className="value">{s ? n(s.dns_seen) : "-"}</div>
          <div className="foot">from {s ? n(s.clients) : "-"} LAN clients</div>
        </div>
        <div className="tile">
          <div className="label">Bypassing the gateway</div>
          <div className="value accent">{bypass.data?.device_count ?? "-"}</div>
          <div className="foot">devices resolving elsewhere</div>
        </div>
        <div className="tile">
          <div className="label">Perimeter drops</div>
          <div className="value">{s ? n(s.perimeter_blocked) : "-"}</div>
          <div className="foot">inbound, not DNS filtering</div>
        </div>
      </div>

      {buckets.length > 1 && (
        <div className="card">
          <div className="card-head"><h2>Activity</h2>
            <span className="pill">hourly</span></div>
          <div style={{ padding: "14px 14px 10px" }}>
            <div className="spark">
              {buckets.map((b) => (
                <div key={b.t} style={{ height: `${(b.events / peak) * 100}%` }}
                     title={`${new Date(b.t).toLocaleString()}
${n(b.events)} events, ${n(b.blocked)} blocked`}>
                  <i style={{ height: `${b.events ? (b.blocked / b.events) * 100 : 0}%` }} />
                </div>
              ))}
            </div>
            <div className="dim" style={{ fontSize: 11.5, marginTop: 6 }}>
              Bar height is total DNS activity; the red portion is what was blocked.
            </div>
          </div>
        </div>
      )}

      <div className="card">
        <div className="card-head"><h2>Top blocked domains</h2>
          <span className="pill">{blockedDomains.data?.domains.length ?? 0}</span></div>
        {(blockedDomains.data?.domains.length ?? 0) === 0
          ? <div className="empty">Nothing blocked in this window.</div>
          : <Bars danger rows={(blockedDomains.data!.domains as StatsDomain[])
              .map((d) => ({ k: d.domain, v: d.events }))} />}
      </div>

      <div className="card">
        <div className="card-head"><h2>Clients by blocked queries</h2></div>
        <table>
          <thead><tr>
            <th>Client</th><th style={{ width: 130 }}>IP</th>
            <th style={{ width: 120 }}>Network</th>
            <th style={{ width: 90 }}>Blocked</th><th style={{ width: 90 }}>Seen</th>
            <th style={{ width: 150 }}>Block rate</th>
          </tr></thead>
          <tbody>
            {(clients.data?.clients ?? []).map((c: StatsClient) => (
              <tr key={c.ip}>
                <td>{c.name}</td>
                <td className="mono dim">{c.ip}</td>
                <td className="dim">{c.network ?? "-"}</td>
                <td className="mono">{n(c.blocked)}</td>
                <td className="mono dim">{n(c.events)}</td>
                <td>
                  <div className="bar-row">
                    <div className="bar-track">
                      <div className="bar-fill danger" style={{ width: `${c.block_rate}%` }} />
                    </div>
                    <span className="mono dim" style={{ minWidth: 42, textAlign: "right" }}>
                      {c.block_rate}%
                    </span>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <div className="card-head">
          <h2>Bypassing the gateway resolver</h2>
          <span className="pill warn">{bypass.data?.device_count ?? 0}</span>
        </div>
        <table>
          <thead><tr>
            <th>Client</th><th style={{ width: 130 }}>IP</th>
            <th style={{ width: 120 }}>Network</th><th>How</th>
            <th style={{ width: 80 }}>Events</th>
          </tr></thead>
          <tbody>
            {(bypass.data?.devices ?? []).map((d) => (
              <tr key={d.ip}>
                <td>{d.name}</td>
                <td className="mono dim">{d.ip}</td>
                <td className="dim">{d.network ?? "-"}</td>
                <td>
                  {[...new Set(d.methods.map((m) => m.kind))].map((k) => (
                    <span key={k} className="tag" style={{ marginRight: 4 }}>{k}</span>
                  ))}
                  <span className="dim mono" style={{ fontSize: 11.5, marginLeft: 4 }}>
                    {[...new Set(d.methods.map((m) => m.dest))].slice(0, 3).join(", ")}
                  </span>
                </td>
                <td className="mono">{n(d.events)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div style={{ padding: "0 14px 14px" }} className="dim">
          These devices never ask the gateway, so nothing on the DNS Settings page
          applies to them. Sinkholed answers are excluded: a destination of
          127.0.0.1 is filtering working, not a device escaping it.
        </div>
      </div>

      <div className="card">
        <div className="card-head"><h2>Most requested, allowed</h2></div>
        {(allowedDomains.data?.domains.length ?? 0) === 0
          ? <div className="empty">No allowed domains recorded yet.</div>
          : <Bars rows={(allowedDomains.data!.domains as StatsDomain[])
              .map((d) => ({ k: d.domain, v: d.events }))} />}
      </div>

      {s && (
        <div className="banner">
          <strong>How to read these numbers.</strong> {s.caveat} Blocked-versus-allowed,
          per-client and per-policy attribution are reliable. Raw query totals are not
          comparable to a Pi-hole query log.
        </div>
      )}
    </>
  );
}
