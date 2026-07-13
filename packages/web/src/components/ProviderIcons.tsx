export function SteamIcon({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" className="provider-icon">
      <path
        fill="currentColor"
        d="M11.979 0C5.678 0 .511 4.86.022 11.037l6.432 2.658c.545-.371 1.207-.59 1.924-.59.063 0 .125.004.188.006l2.861-4.142V8.4c0-2.495 2.028-4.522 4.523-4.522 2.494 0 4.522 2.027 4.522 4.522 0 2.495-2.028 4.523-4.522 4.523h-.105l-4.076 2.911c0 .052.004.105.004.159 0 1.875-1.515 3.396-3.387 3.396-1.635 0-3.016-1.173-3.331-2.727L.436 15.12C1.974 20.311 6.692 24 11.979 24c6.627 0 11.999-5.373 11.999-12S18.606 0 11.979 0zM7.54 18.351l-1.331-.551c.278.596.868 1.009 1.549 1.009.968 0 1.753-.785 1.753-1.753 0-.968-.785-1.753-1.753-1.753-.674 0-1.259.381-1.553.938l1.331.551-.411 1.458zm11.387-9.494c0-1.662-1.353-3.015-3.015-3.015-1.662 0-3.015 1.353-3.015 3.015 0 1.662 1.353 3.015 3.015 3.015 1.662 0 3.015-1.353 3.015-3.015z"
      />
    </svg>
  );
}

export function LeetifyIcon({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" className="provider-icon">
      <rect x="3" y="3" width="18" height="18" rx="4" fill="currentColor" opacity="0.22" />
      <path
        fill="currentColor"
        d="M7 16V8h2.2l2.4 4.6L14 8h2.2v8h-1.8v-4.8L11.2 16H9.8L7.8 11.2V16H7zm8.5 0V8H19v8h-3.5z"
      />
    </svg>
  );
}

export function FaceitIcon({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" className="provider-icon">
      <rect x="3" y="3" width="18" height="18" rx="4" fill="currentColor" opacity="0.22" />
      <path
        fill="currentColor"
        d="M7.5 7h9v2.2h-3.4V17H10.9V9.2H7.5V7zm5.8 0H19v2.2h-2.1l1.4 3.6 1.4-3.6H19V17h-2.2v-5.8L15.4 17h-1.8l-1.4-3.6L10.8 17H8.6l2.1-5.3L7.5 7h5.8z"
      />
    </svg>
  );
}
