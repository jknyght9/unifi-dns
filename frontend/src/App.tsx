import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { DriftPage, HistoryPage, RecordsPage, SettingsPage } from "./pages";
import { DnsSettingsPage } from "./DnsSettingsPage";
import { MigratePage } from "./MigratePage";
import { DashboardPage } from "./DashboardPage";
import { LoginGate } from "./LoginGate";
import {
  IconDashboard, IconDrift, IconHistory, IconMenu, IconMigrate, IconUnlocked,
  IconRecords, IconSettings, IconZones,
} from "./icons";

type Tab = "dashboard" | "records" | "history" | "drift" | "dns" | "apexes" | "migrate";

const TABS: { id: Tab; label: string; icon: () => React.ReactElement }[] = [
  { id: "dashboard", label: "Gateway DNS", icon: IconDashboard },
  { id: "records", label: "DNS Records", icon: IconRecords },
  { id: "history", label: "History", icon: IconHistory },
  { id: "drift", label: "Drift", icon: IconDrift },
  { id: "dns", label: "DNS Settings", icon: IconSettings },
  { id: "apexes", label: "Apex domains", icon: IconZones },
  { id: "migrate", label: "Migrate", icon: IconMigrate },
];

const initials = (name?: string | null) =>
  (name ?? "?").split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();

const TAB_IDS: Tab[] = [
  "dashboard", "records", "history", "drift", "dns", "apexes", "migrate",
];

/** Tab lives in the URL hash so pages are linkable and the back button works. */
function tabFromHash(): Tab {
  const h = window.location.hash.replace(/^#\/?/, "") as Tab;
  return TAB_IDS.includes(h) ? h : "dashboard";
}

export default function App() {
  const [tab, setTabState] = useState<Tab>(tabFromHash);

  useEffect(() => {
    const onHash = () => setTabState(tabFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const setTab = (t: Tab) => {
    window.location.hash = `#/${t}`;
    setTabState(t);
  };
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem("nav.collapsed") === "1",
  );
  // Identity is resolved before anything else renders: an unauthenticated user
  // should see a sign-in screen, not an app that fails every request.
  const me = useQuery({ queryKey: ["me"], queryFn: api.me, retry: false });
  const authed = me.data?.authenticated ?? false;
  const status = useQuery({
    queryKey: ["status"], queryFn: api.status,
    refetchInterval: 30_000, enabled: authed,
  });

  const toggle = () => {
    setCollapsed((c) => {
      localStorage.setItem("nav.collapsed", c ? "0" : "1");
      return !c;
    });
  };

  if (me.isLoading) return <div className="gate" />;
  if (me.data && !me.data.authenticated) return <LoginGate auth={me.data} />;

  const up = status.data?.unifi_reachable;
  const who = me.data?.user?.name ?? status.data?.unifi_admin ?? null;
  const unprotected = me.data?.mode === "open";

  return (
    <div className="shell">
      <header className="topbar">
        <button className="icon-btn" onClick={toggle}
                title={collapsed ? "Expand navigation" : "Collapse navigation"}
                aria-label="Toggle navigation">
          <IconMenu />
        </button>
        <div className="brand">unifi<span>-dns</span></div>
        <div className="divider" />
        <div className="meta">
          <span className={`status-dot ${up ? "up" : "down"}`} />
          {status.isLoading ? "Connecting..." : up ? "Gateway Online" : "Gateway Unreachable"}
        </div>
        {status.data?.application_version && (
          <>
            <div className="divider" />
            <div className="meta dim mono">Network {status.data.application_version}</div>
          </>
        )}
        <div className="spacer" />
        {unprotected ? (
          // There is no user to show, so showing an empty avatar would be a lie.
          // State the deployment fact instead, and explain it on hover.
          <div
            className="open-access"
            title={
              "No sign-in is configured, so anyone who can reach this address has " +
              "full access.\n\nSet OIDC_ISSUER (Authentik, Keycloak, Pocket ID, " +
              "Authelia) or TRUSTED_USER_HEADER if a proxy authenticates in front."
            }
          >
            <IconUnlocked />
            <span>Open access</span>
          </div>
        ) : (
          <div className="user">
            {who && <span>{who}</span>}
            <div className="avatar" title={who ?? "signed in"}>{initials(who)}</div>
          </div>
        )}
      </header>

      <div className="body">
        <nav className={`sidebar ${collapsed ? "collapsed" : ""}`}>
          {TABS.map((t) => {
            const Icon = t.icon;
            return (
              <button key={t.id} title={t.label}
                      className={`navbtn ${tab === t.id ? "active" : ""}`}
                      onClick={() => setTab(t.id)}>
                <Icon />
                {!collapsed && <span>{t.label}</span>}
              </button>
            );
          })}
        </nav>

        <main className="main">
          {status.data && !status.data.unifi_reachable && (
            <div className="banner err">
              Cannot reach the UniFi console
              {status.data.unifi_error ? `: ${status.data.unifi_error.message}` : "."}
            </div>
          )}
          {tab === "dashboard" && <DashboardPage />}
          {tab === "records" && <RecordsPage />}
          {tab === "history" && <HistoryPage />}
          {tab === "drift" && <DriftPage />}
          {tab === "dns" && <DnsSettingsPage />}
          {tab === "apexes" && <SettingsPage />}
          {tab === "migrate" && <MigratePage />}
        </main>
      </div>
    </div>
  );
}
