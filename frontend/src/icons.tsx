/** Inline 16px icons. Inlined rather than pulled from a package so the bundle
 *  stays dependency-free and the CSP-friendly single-file build holds. */
const P = ({ d }: { d: string }) => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4"
       strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d={d} />
  </svg>
);

export const IconRecords = () => <P d="M2 4h12M2 8h12M2 12h8" />;
export const IconHistory = () => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4"
       strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <circle cx="8" cy="8" r="6" /><path d="M8 4.6V8l2.4 1.6" />
  </svg>
);
export const IconDrift = () => <P d="M2 5h6M2 11h6M14 5h-3M14 11h-3M10.5 2.5 13 5l-2.5 2.5M5.5 8.5 3 11l2.5 2.5" />;
export const IconSettings = () => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4"
       strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <circle cx="8" cy="8" r="2.2" />
    <path d="M8 1.6v1.6M8 12.8v1.6M14.4 8h-1.6M3.2 8H1.6M12.5 3.5l-1.1 1.1M4.6 11.4l-1.1 1.1M12.5 12.5l-1.1-1.1M4.6 4.6 3.5 3.5" />
  </svg>
);
export const IconZones = () => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4"
       strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <circle cx="8" cy="8" r="6" /><path d="M2 8h12M8 2c1.8 2 1.8 10 0 12M8 2c-1.8 2-1.8 10 0 12" />
  </svg>
);
export const IconMigrate = () => <P d="M2 8h9M8 5l3 3-3 3M12.5 2.5h1.5v11h-1.5" />;
export const IconMenu = () => <P d="M2 4h12M2 8h12M2 12h12" />;
export const IconDashboard = () => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4"
       strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <rect x="2" y="2" width="5" height="6" rx="1" />
    <rect x="9" y="2" width="5" height="4" rx="1" />
    <rect x="2" y="10" width="5" height="4" rx="1" />
    <rect x="9" y="8" width="5" height="6" rx="1" />
  </svg>
);

export const IconCaret = ({ open }: { open: boolean }) => (
  <svg className={`caret ${open ? "" : "closed"}`} viewBox="0 0 16 16" fill="none"
       stroke="currentColor" strokeWidth="2" strokeLinecap="round"
       strokeLinejoin="round" aria-hidden="true">
    <path d="M4 6l4 4 4-4" />
  </svg>
);

/** Open padlock: the deployment has no sign-in configured. */
export const IconUnlocked = () => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4"
       strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <rect x="3" y="7" width="10" height="7" rx="1.6" />
    <path d="M5.6 7V4.9a2.4 2.4 0 0 1 4.8 0" />
  </svg>
);
