import { useState } from "react";
import { RECORD_TYPES, TTL_CAPABLE, shortType } from "./types";
import type { RecordType, RenderedRecord } from "./types";

/** Build an Integration v1 payload.
 *  TTL is omitted for MX/TXT/SRV because the gateway rejects the property
 *  outright rather than ignoring it, and SRV needs its label split into
 *  service/protocol/domain. */
function toPayload(
  type: RecordType, fqdn: string, value: string, enabled: boolean,
  ttl: number, priority: number, weight: number, port: number,
): Record<string, unknown> {
  const base: Record<string, unknown> = { type, enabled, domain: fqdn.trim().toLowerCase() };
  if (TTL_CAPABLE.has(type)) base.ttlSeconds = ttl;
  switch (type) {
    case "A_RECORD": return { ...base, ipv4Address: value.trim() };
    case "AAAA_RECORD": return { ...base, ipv6Address: value.trim() };
    case "CNAME_RECORD": return { ...base, targetDomain: value.trim() };
    case "MX_RECORD": return { ...base, mailServerDomain: value.trim(), priority };
    case "TXT_RECORD": return { ...base, text: value };
    case "SRV_RECORD": {
      const parts = fqdn.trim().toLowerCase().split(".");
      const [service, protocol, ...rest] = parts;
      return {
        ...base, domain: rest.join("."), service, protocol,
        serverDomain: value.trim(), priority, weight, port,
      };
    }
  }
}

const VALUE_LABEL: Record<RecordType, string> = {
  A_RECORD: "IPv4 address", AAAA_RECORD: "IPv6 address",
  CNAME_RECORD: "Target domain", MX_RECORD: "Mail server",
  TXT_RECORD: "Text", SRV_RECORD: "Server domain",
};

export function RecordEditor(
  { existing, onCancel, onSave, busy, error }: {
    existing?: RenderedRecord | null;
    onCancel: () => void;
    onSave: (payload: Record<string, unknown>) => void;
    busy: boolean;
    error: string | null;
  },
) {
  const raw = (existing?.raw ?? {}) as Record<string, any>;
  const [type, setType] = useState<RecordType>(existing?.type ?? "A_RECORD");
  const [fqdn, setFqdn] = useState(existing?.fqdn ?? "");
  const [value, setValue] = useState(
    raw.ipv4Address ?? raw.ipv6Address ?? raw.targetDomain ??
    raw.mailServerDomain ?? raw.text ?? raw.serverDomain ?? "",
  );
  const [enabled, setEnabled] = useState(existing?.enabled ?? true);
  const [ttl, setTtl] = useState(existing?.ttl_seconds ?? 300);
  const [priority, setPriority] = useState(raw.priority ?? 10);
  const [weight, setWeight] = useState(raw.weight ?? 0);
  const [port, setPort] = useState(raw.port ?? 0);

  const isSrv = type === "SRV_RECORD";
  const srvMalformed = isSrv && fqdn.split(".").length < 3;

  return (
    <div className="modal-back" onClick={onCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>{existing ? "Edit record" : "New record"}</h3>
        <div className="modal-body">
          {error && <div className="banner err">{error}</div>}
          <div className="row2">
            <label>
              Type
              <select value={type} onChange={(e) => setType(e.target.value as RecordType)}
                      disabled={!!existing}>
                {RECORD_TYPES.map((t) => <option key={t} value={t}>{shortType(t)}</option>)}
              </select>
            </label>
            <label>
              TTL (seconds, 0 = auto)
              <input type="number" min={0} value={ttl}
                     onChange={(e) => setTtl(Number(e.target.value))}
                     disabled={!TTL_CAPABLE.has(type)} />
            </label>
          </div>
          {!TTL_CAPABLE.has(type) && (
            <div className="dim" style={{ fontSize: 12, marginTop: -6 }}>
              UniFi rejects a TTL on {shortType(type)} records, so it is omitted.
            </div>
          )}
          <label>
            {isSrv ? "Full name (_service._proto.domain)" : "Name (FQDN)"}
            <input value={fqdn} onChange={(e) => setFqdn(e.target.value)}
                   placeholder={isSrv ? "_sip._tcp.example.internal" : "nas.example.internal"}
                   className="mono" />
          </label>
          {srvMalformed && (
            <div className="banner warn">
              SRV needs the <code>_service._proto.name</code> form. UniFi stores these
              as three separate fields.
            </div>
          )}
          <label>
            {VALUE_LABEL[type]}
            {type === "TXT_RECORD"
              ? <textarea rows={3} value={value} onChange={(e) => setValue(e.target.value)} className="mono" />
              : <input value={value} onChange={(e) => setValue(e.target.value)} className="mono" />}
          </label>
          {(type === "MX_RECORD" || isSrv) && (
            <div className={isSrv ? "row3" : ""}>
              <label>Priority
                <input type="number" value={priority} onChange={(e) => setPriority(Number(e.target.value))} />
              </label>
              {isSrv && <label>Weight
                <input type="number" value={weight} onChange={(e) => setWeight(Number(e.target.value))} />
              </label>}
              {isSrv && <label>Port
                <input type="number" min={0} max={65535} value={port}
                       onChange={(e) => setPort(Number(e.target.value))} />
              </label>}
            </div>
          )}
          <label style={{ flexDirection: "row", alignItems: "center", display: "flex", gap: 8 }}>
            <input type="checkbox" checked={enabled} style={{ width: "auto" }}
                   onChange={(e) => setEnabled(e.target.checked)} />
            Enabled
          </label>
        </div>
        <div className="modal-foot">
          <button className="btn" onClick={onCancel} disabled={busy}>Cancel</button>
          <button className="btn primary" disabled={busy || !fqdn || !value || srvMalformed}
                  onClick={() => onSave(toPayload(type, fqdn, value, enabled, ttl, priority, weight, port))}>
            {busy ? "Saving..." : existing ? "Save changes" : "Create"}
          </button>
        </div>
      </div>
    </div>
  );
}
