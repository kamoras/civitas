"use client";

import { useState } from "react";
import Link from "next/link";
import VisitorCounter from "@/components/effects/VisitorCounter";

function DigestSubscribeForm() {
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "ok" | "err">("idle");
  const [msg, setMsg] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email.trim() || status === "loading") return;
    setStatus("loading");
    try {
      const res = await fetch("/api/alerts/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim(), topics: [], senators: [] }),
      });
      const data = await res.json();
      if (res.ok && data.ok) {
        setStatus("ok");
        setMsg(data.message || "Subscribed!");
        setEmail("");
      } else {
        setStatus("err");
        setMsg(data.detail || "Something went wrong.");
      }
    } catch {
      setStatus("err");
      setMsg("Could not connect. Please try again.");
    }
  }

  if (status === "ok") {
    return (
      <p className="text-xs text-emerald-400/80 text-center">{msg}</p>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col items-center gap-2 w-full max-w-sm">
      <p className="text-[10px] font-pixel text-matrix-green/40 tracking-wider">WEEKLY DIGEST</p>
      <div className="flex w-full gap-2">
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="your@email.com"
          required
          className="flex-1 min-w-0 bg-crt-black border border-matrix-green/20 text-matrix-green text-xs px-3 py-2
                     placeholder:text-matrix-green/25 focus:outline-none focus:border-emerald-500/50 transition-colors"
        />
        <button
          type="submit"
          disabled={status === "loading"}
          className="text-[10px] font-pixel px-3 py-2 border border-emerald-500/40 text-emerald-400
                     hover:bg-emerald-500/10 transition-colors disabled:opacity-40 shrink-0"
        >
          {status === "loading" ? "..." : "SUBSCRIBE"}
        </button>
      </div>
      {status === "err" && (
        <p className="text-[10px] text-red-400/70">{msg}</p>
      )}
      <p className="text-[9px] text-matrix-green/25 text-center">
        Weekly summary of votes, monitors, and open comment periods. No spam. One-click unsubscribe.
      </p>
    </form>
  );
}

export default function Footer() {
  return (
    <footer className="border-t border-matrix-green/20 bg-crt-black/80 py-8 px-4">
      <div className="max-w-4xl mx-auto flex flex-col items-center gap-6">
        {/* Links */}
        <nav aria-label="Footer navigation" className="flex flex-wrap justify-center gap-6">
          <Link
            href="/scorecard"
            className="text-matrix-green/40 hover:text-matrix-green/80 transition-colors font-mono text-xs tracking-widest uppercase"
          >
            SCORECARD
          </Link>
          <Link
            href="/about"
            className="text-matrix-green/40 hover:text-matrix-green/80 transition-colors font-mono text-xs tracking-widest uppercase"
          >
            METHODOLOGY
          </Link>
          <Link
            href="/accessibility"
            className="text-matrix-green/40 hover:text-matrix-green/80 transition-colors font-mono text-xs tracking-widest uppercase"
          >
            ACCESSIBILITY
          </Link>
        </nav>

        {/* Digest subscribe */}
        <DigestSubscribeForm />

        {/* Visitor Counter */}
        <VisitorCounter />

        {/* Disclaimer */}
        <p className="text-xs text-matrix-green/50 max-w-lg text-center leading-relaxed">
          All data sourced from public records: FEC campaign finance filings (fec.gov),
          OpenSecrets.org donor &amp; industry data, GovTrack.us &amp; MapLight voting records, and
          Senate Lobbying Disclosure Act filings (lda.senate.gov). The Representation Scorecard is
          a weighted composite metric — not a measure of illegality or wrongdoing. Correlation
          between donations and votes does not prove causation. Verify all data at the original
          sources. Draw your own conclusions.
        </p>
      </div>
    </footer>
  );
}
