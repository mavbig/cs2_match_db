"use client";

import { useState } from "react";
import type { MatchGcDebug } from "@/lib/api";

export function MatchDebugPanel({ debug }: { debug: MatchGcDebug }) {
  const [copied, setCopied] = useState(false);
  const json = JSON.stringify(debug, null, 2);

  async function copyJson() {
    await navigator.clipboard.writeText(json);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="card debug-panel">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.75rem" }}>
        <h2 style={{ fontSize: "1.05rem" }}>GC Debug Data</h2>
        <button type="button" className="btn" onClick={copyJson}>
          {copied ? "Copied!" : "Copy JSON"}
        </button>
      </div>
      <p style={{ color: "var(--muted)", fontSize: "0.85rem", marginBottom: "0.75rem" }}>
        Paste this when reporting parsing issues. Includes stored values, parse hints, and the raw GC payload.
      </p>
      <pre>{json}</pre>
    </div>
  );
}
