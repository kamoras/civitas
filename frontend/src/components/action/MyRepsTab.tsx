"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { SCORE_TERMS } from "@/lib/scoreTerms";
import { fetchMyReps, fetchActionIssues } from "@/lib/api";
import { STATES } from "@/data/states";
import { PARTY_COLORS, PARTY_BORDER, PARTY_BG } from "@/lib/partyStyles";
import { getScoreBgColor } from "@/lib/representation";
import { useCopyFeedback } from "@/hooks/useCopyFeedback";
import type { ActionIssue, MyRepSenator, MyRepsResponse } from "@/types/action";

function ContactScript({
  name,
  stateName,
  phone,
  contactFormUrl,
}: {
  name: string;
  stateName: string;
  phone?: string | null;
  contactFormUrl?: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [copied, copy] = useCopyFeedback(2000);

  // One source for both the copyable plain text and the highlighted JSX, so
  // the two can't drift. `fill` segments are the user-replaceable placeholders.
  const scriptSegments: { text: string; fill?: boolean }[] = [
    { text: "My name is " },
    { text: "[YOUR NAME]", fill: true },
    {
      text: ` and I am a constituent from ${stateName}. I am calling to express my concern about `,
    },
    { text: "[ISSUE]", fill: true },
    { text: `. I urge ${name} to ` },
    { text: "[TAKE ACTION]", fill: true },
    { text: ". Please leave a record of this call. Thank you." },
  ];
  const script = scriptSegments.map((s) => s.text).join("");

  if (!phone && !contactFormUrl) return null;

  function copyScript() {
    copy(script);
  }

  return (
    <div className="mt-3 border-t border-white/[0.07] pt-3">
      <div className="flex flex-wrap items-center gap-2 mb-2">
        {phone && (
          <a
            href={`tel:${phone.replace(/[^0-9+]/g, "")}`}
            className="inline-flex items-center gap-1 px-2.5 py-1 border border-white/[0.07] text-ink-lo font-mono text-xs hover:bg-white/[0.03] transition-colors"
          >
            CALL: {phone}
          </a>
        )}
        {contactFormUrl && (
          <a
            href={contactFormUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 px-2.5 py-1 border border-white/15 text-ink-lo font-mono text-xs hover:bg-signal-cyan/10 transition-colors"
          >
            CONTACT FORM <span aria-hidden="true">↗</span>
          </a>
        )}
        <button
          onClick={() => setOpen((v) => !v)}
          className="font-mono text-xs text-ink-lo hover:text-signal-amber border border-signal-amber/40 px-2.5 py-1 transition-colors"
          aria-expanded={open}
        >
          {open ? "HIDE SCRIPT" : "GET SCRIPT"}
        </button>
      </div>

      {open && (
        <div className="bg-white/[0.03] border border-white/[0.07] p-3 space-y-2">
          <p className="text-xs text-ink leading-relaxed font-mono">
            {scriptSegments.map((seg, i) =>
              seg.fill ? (
                <span key={i} className="text-signal-amber">
                  {seg.text}
                </span>
              ) : (
                <span key={i}>{seg.text}</span>
              )
            )}
          </p>
          <button
            onClick={copyScript}
            className="font-mono text-xs border px-2.5 py-1 transition-colors"
            style={{
              borderColor: copied ? "#00ff41" : "rgba(0,255,65,0.3)",
              color: copied ? "#00ff41" : "rgba(0,255,65,0.6)",
            }}
          >
            {copied ? "COPIED!" : "COPY SCRIPT"}
          </button>
          <p className="text-xs text-ink-min italic">
            Replace bracketed text with your own words before calling.
          </p>
        </div>
      )}
    </div>
  );
}

function ScoreBar({ label, value }: { label: string; value: number }) {
  const color = getScoreBgColor(value);

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-ink-lo w-20 shrink-0 font-mono truncate">{label}</span>
      <div className="flex-1 h-2 bg-white/[0.03] overflow-hidden">
        <div
          className={`h-full ${color} transition-all duration-500`}
          style={{ width: `${Math.min(value, 100)}%` }}
        />
      </div>
      <span className="text-xs text-ink-lo font-mono w-8 text-right">{Math.round(value)}</span>
    </div>
  );
}

// One card for both senators and representatives — the House-only district
// (shown as `STATE-NN` and a DISTRICT line) is the sole difference.
function RepresentativeCard({ person, district }: { person: MyRepSenator; district?: number }) {
  const s = person.scores;

  return (
    <div
      className={`terminal-window border ${PARTY_BORDER[person.party]} ${PARTY_BG[person.party]} p-5`}
    >
      <div className="flex items-start justify-between gap-3 mb-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span
              className={`font-mono text-xs px-1.5 py-0.5 border ${PARTY_BORDER[person.party]} ${PARTY_COLORS[person.party]}`}
            >
              {person.party}
            </span>
            <span className="text-ink-min text-xs font-mono">
              {district != null ? `${person.state}-${district}` : person.state}
            </span>
            {person.yearsInOffice > 0 && (
              <span className="text-ink-lo text-xs font-mono">
                {person.yearsInOffice}yr{person.yearsInOffice !== 1 ? "s" : ""}
              </span>
            )}
          </div>
          <h3 className="font-display font-semibold text-base sm:text-lg text-ink-hi leading-snug">
            {person.name}
          </h3>
          {district != null && (
            <div className="text-xs text-ink-lo font-mono mt-0.5">DISTRICT {district}</div>
          )}
        </div>
        <div className="text-right shrink-0">
          <div className="font-display font-semibold text-2xl text-ink-hi">
            {Math.round(s.overall)}
          </div>
          <div className="text-xs text-ink-min font-mono">OVERALL</div>
        </div>
      </div>

      {/* v6.5: fundingDiversity folded into fundingIndependence, no longer its own dimension */}
      <div className="space-y-1.5 mb-4">
        <ScoreBar
          label={SCORE_TERMS["fundingIndependence"].shortLabel}
          value={s.fundingIndependence}
        />
        <ScoreBar label={SCORE_TERMS["independentVoting"].shortLabel} value={s.independentVoting} />
        <ScoreBar
          label={SCORE_TERMS["legislativeEffectiveness"].shortLabel}
          value={s.legislativeEffectiveness}
        />
      </div>

      {person.connectedIssues.length > 0 && (
        <div className="border-t border-white/[0.07] pt-3 mb-3">
          <h4 className="font-mono text-xs text-ink-lo mb-2">CONNECTED TO TODAY&apos;S ISSUES</h4>
          <div className="space-y-1.5">
            {person.connectedIssues.map((iss) => (
              <div key={iss.id} className="flex items-start gap-2 text-sm">
                <span className="text-xs font-mono text-ink-lo shrink-0 mt-0.5">#{iss.rank}</span>
                <span className="text-ink leading-snug">{iss.title}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <Link
        href={`/politicians/${person.id}`}
        className="inline-block font-mono text-xs text-signal-cyan border border-white/15 px-3 py-1.5 hover:bg-signal-cyan/10 transition-colors"
      >
        VIEW FULL SCORECARD →
      </Link>

      <ContactScript
        name={person.name}
        stateName={STATES.find((st) => st.code === person.state)?.name || person.state}
        phone={person.officePhone}
        contactFormUrl={person.contactFormUrl}
      />
    </div>
  );
}

export default function MyRepsTab({
  userState,
  setUserState,
  issues,
}: {
  userState: string | null;
  setUserState: (s: string | null) => void;
  /** Optional pre-fetched issues from the parent; avoids a redundant fetchActionIssues() call. */
  issues?: ActionIssue[];
}) {
  const [data, setData] = useState<MyRepsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [fetchError, setFetchError] = useState(false);
  const [fallbackIssues, setFallbackIssues] = useState<ActionIssue[]>([]);
  const activeIssues = issues ?? fallbackIssues;

  const loadReps = useCallback((st: string) => {
    setLoading(true);
    setFetchError(false);
    fetchMyReps(st)
      .then((d) => setData(d))
      .catch(() => setFetchError(true))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (userState) loadReps(userState);
  }, [userState, loadReps]);

  useEffect(() => {
    if (issues) return;
    fetchActionIssues()
      .then((d) => setFallbackIssues(d.issues || []))
      .catch(() => {});
  }, [issues]);

  const repIssues = useMemo(() => {
    if (!data || activeIssues.length === 0) return [];
    // Issues the user's own reps are connected to, via the backend-precomputed
    // per-member connectedIssues (covers both senators and House reps — the
    // prior relatedSenators filter missed the House), intersected with what's
    // currently trending. Depends on activeIssues so it recomputes when the
    // async fallback fetch resolves (issues prop absent).
    const connectedIds = new Set<number>();
    data.senators.forEach((s) => s.connectedIssues.forEach((i) => connectedIds.add(i.id)));
    (data.representatives ?? []).forEach((r) =>
      r.connectedIssues.forEach((i) => connectedIds.add(i.id))
    );
    return activeIssues.filter((iss) => connectedIds.has(iss.id));
  }, [activeIssues, data]);

  if (!userState) {
    return (
      <div className="terminal-window max-w-md mx-auto p-8 text-center space-y-5">
        <div className="font-mono text-sm text-signal-cyan mb-2">SELECT YOUR STATE</div>
        <p className="text-ink-lo text-base leading-relaxed">
          Choose your state to see your senators, their scores, and how they connect to today&apos;s
          top issues.
        </p>
        <select
          value=""
          onChange={(e) => setUserState(e.target.value || null)}
          className="w-full bg-white/[0.03] border border-white/15 text-ink-hi px-4 py-3 font-mono text-sm focus:outline-none focus:border-signal-cyan/40"
          aria-label="Select your state"
        >
          <option value="">— CHOOSE STATE —</option>
          {STATES.map((s) => (
            <option key={s.code} value={s.code}>
              {s.code} — {s.name}
            </option>
          ))}
        </select>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <div className="text-signal-cyan animate-pulse font-mono text-sm">
          {">"} LOADING YOUR REPRESENTATIVES...
        </div>
      </div>
    );
  }

  if (fetchError) {
    return (
      <div className="terminal-window max-w-lg mx-auto p-8 text-center space-y-4" role="alert">
        <div className="font-mono text-sm text-signal-red">CONNECTION ERROR</div>
        <p className="text-ink-lo text-base">Could not load representative data.</p>
        <button
          onClick={() => loadReps(userState)}
          className="text-signal-cyan font-mono text-sm border border-white/15 px-4 py-2 hover:bg-signal-cyan/10 transition-colors"
        >
          [RETRY]
        </button>
      </div>
    );
  }

  const stateName = STATES.find((s) => s.code === userState)?.name || userState;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="font-mono text-sm sm:text-base text-ink-hi">
            YOUR REPRESENTATIVES — {stateName.toUpperCase()}
          </h2>
          {data?.issueDate && (
            <div className="text-xs text-ink-min font-mono mt-1">ISSUES FROM {data.issueDate}</div>
          )}
        </div>
        <button
          onClick={() => setUserState(null)}
          className="font-mono text-xs text-ink-min hover:text-phos border border-white/[0.07] px-2 py-1 transition-colors"
          aria-label="Change state"
        >
          CHANGE STATE
        </button>
      </div>

      {repIssues.length > 0 && (
        <div className="terminal-window border border-signal-magenta/40 bg-signal-magenta/10 p-4 space-y-3">
          <div className="font-mono text-xs text-ink-lo">{">"} YOUR REPS IN THE NEWS</div>
          <div className="space-y-2">
            {repIssues.map((iss) => (
              <Link
                key={iss.id}
                href={`/action?issue=${iss.id}`}
                className="flex items-start gap-3 group hover:bg-signal-magenta/10 transition-colors p-2 -mx-2"
              >
                <span className="text-xs font-mono text-ink-lo shrink-0 mt-0.5">#{iss.rank}</span>
                <span className="text-sm text-ink group-hover:text-phos leading-snug">
                  {iss.title}
                </span>
                <span
                  className="text-xs font-mono text-ink-lo shrink-0 ml-auto mt-0.5"
                  aria-hidden="true"
                >
                  →
                </span>
              </Link>
            ))}
          </div>
        </div>
      )}

      {data &&
      (data.senators.length > 0 || (data.representatives && data.representatives.length > 0)) ? (
        <div className="space-y-6">
          {data.senators.length > 0 && (
            <div className="space-y-4">
              <div className="font-mono text-xs text-ink-lo">{">"} SENATORS</div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {data.senators.map((senator) => (
                  <RepresentativeCard key={senator.id} person={senator} />
                ))}
              </div>
            </div>
          )}

          {data.representatives && data.representatives.length > 0 && (
            <div className="space-y-4">
              <div className="font-mono text-xs text-ink-lo">{">"} HOUSE REPRESENTATIVES</div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {data.representatives.map((rep) => (
                  <RepresentativeCard key={rep.id} person={rep} district={rep.district} />
                ))}
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="terminal-window p-8 text-center">
          <div className="font-mono text-sm text-signal-amber">NO REPRESENTATIVE DATA</div>
          <p className="text-ink-lo text-base mt-2">
            Senator and representative data for {stateName} is not yet available. Run the pipeline
            to populate scores.
          </p>
        </div>
      )}
    </div>
  );
}
