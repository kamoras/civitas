"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { SCORE_TERMS } from "@/lib/scoreTerms";
import { fetchMyReps, fetchActionIssues } from "@/lib/api";
import { STATES } from "@/data/states";
import { PARTY_COLORS, PARTY_BORDER, PARTY_BG } from "@/lib/partyStyles";
import type { ActionIssue, MyRepRep, MyRepSenator, MyRepsResponse } from "@/types/action";

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
  const [copied, setCopied] = useState(false);
  const script = `My name is [YOUR NAME] and I am a constituent from ${stateName}. I am calling to express my concern about [ISSUE]. I urge ${name} to [TAKE ACTION]. Please leave a record of this call. Thank you.`;

  if (!phone && !contactFormUrl) return null;

  async function copyScript() {
    try {
      await navigator.clipboard.writeText(script);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // clipboard not available
    }
  }

  return (
    <div className="mt-3 border-t border-matrix-green/10 pt-3">
      <div className="flex flex-wrap items-center gap-2 mb-2">
        {phone && (
          <a
            href={`tel:${phone.replace(/[^0-9+]/g, "")}`}
            className="inline-flex items-center gap-1 px-2.5 py-1 border border-matrix-green/20 text-matrix-green/60 font-pixel text-[10px] hover:bg-matrix-green/5 transition-colors"
          >
            CALL: {phone}
          </a>
        )}
        {contactFormUrl && (
          <a
            href={contactFormUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 px-2.5 py-1 border border-neon-cyan/20 text-neon-cyan/60 font-pixel text-[10px] hover:bg-neon-cyan/5 transition-colors"
          >
            CONTACT FORM <span aria-hidden="true">↗</span>
          </a>
        )}
        <button
          onClick={() => setOpen((v) => !v)}
          className="font-pixel text-[10px] text-neon-yellow/60 hover:text-neon-yellow border border-neon-yellow/20 px-2.5 py-1 transition-colors"
          aria-expanded={open}
        >
          {open ? "HIDE SCRIPT" : "GET SCRIPT"}
        </button>
      </div>

      {open && (
        <div className="bg-matrix-dark-green/20 border border-matrix-green/20 p-3 space-y-2">
          <p className="text-[11px] text-matrix-green/80 leading-relaxed font-mono">
            {`My name is `}
            <span className="text-neon-yellow/90">[YOUR NAME]</span>
            {` and I am a constituent from ${stateName}. I am calling to express my concern about `}
            <span className="text-neon-yellow/90">[ISSUE]</span>
            {`. I urge ${name} to `}
            <span className="text-neon-yellow/90">[TAKE ACTION]</span>
            {`. Please leave a record of this call. Thank you.`}
          </p>
          <button
            onClick={copyScript}
            className="font-pixel text-[10px] border px-2.5 py-1 transition-colors"
            style={{
              borderColor: copied ? "#00ff41" : "rgba(0,255,65,0.3)",
              color: copied ? "#00ff41" : "rgba(0,255,65,0.6)",
            }}
          >
            {copied ? "COPIED!" : "COPY SCRIPT"}
          </button>
          <p className="text-[9px] text-matrix-green/30 italic">
            Replace bracketed text with your own words before calling.
          </p>
        </div>
      )}
    </div>
  );
}

function ScoreBar({ label, value }: { label: string; value: number }) {
  const color =
    value >= 70 ? "bg-green-400/60" : value >= 40 ? "bg-amber-400/60" : "bg-red-400/60";

  return (
    <div className="flex items-center gap-2">
      <span className="text-[10px] text-matrix-green/50 w-20 shrink-0 font-pixel truncate">
        {label}
      </span>
      <div className="flex-1 h-2 bg-matrix-green/10 overflow-hidden">
        <div
          className={`h-full ${color} transition-all duration-500`}
          style={{ width: `${Math.min(value, 100)}%` }}
        />
      </div>
      <span className="text-[10px] text-matrix-green/60 font-pixel w-8 text-right">
        {Math.round(value)}
      </span>
    </div>
  );
}

function SenatorCard({ senator }: { senator: MyRepSenator }) {
  const s = senator.scores;

  return (
    <div
      className={`terminal-window border ${PARTY_BORDER[senator.party]} ${PARTY_BG[senator.party]} p-5`}
    >
      <div className="flex items-start justify-between gap-3 mb-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span
              className={`font-pixel text-xs px-1.5 py-0.5 border ${PARTY_BORDER[senator.party]} ${PARTY_COLORS[senator.party]}`}
            >
              {senator.party}
            </span>
            <span className="text-matrix-green/40 text-[10px] font-pixel">
              {senator.state}
            </span>
            {senator.yearsInOffice > 0 && (
              <span className="text-matrix-green/50 text-[10px] font-pixel">
                {senator.yearsInOffice}yr{senator.yearsInOffice !== 1 ? "s" : ""}
              </span>
            )}
          </div>
          <h3 className="font-pixel text-base sm:text-lg text-matrix-green leading-snug">
            {senator.name}
          </h3>
        </div>
        <div className="text-right shrink-0">
          <div className="font-pixel text-2xl text-matrix-green">{Math.round(s.overall)}</div>
          <div className="text-[10px] text-matrix-green/40 font-pixel">OVERALL</div>
        </div>
      </div>

      <div className="space-y-1.5 mb-4">
        <ScoreBar label={SCORE_TERMS["fundingIndependence"].shortLabel} value={s.fundingIndependence} />
        <ScoreBar label={SCORE_TERMS["independentVoting"].shortLabel} value={s.independentVoting} />
        <ScoreBar label={SCORE_TERMS["fundingDiversity"].shortLabel} value={s.fundingDiversity} />
        <ScoreBar label={SCORE_TERMS["legislativeEffectiveness"].shortLabel} value={s.legislativeEffectiveness} />
      </div>

      {senator.connectedIssues.length > 0 && (
        <div className="border-t border-matrix-green/10 pt-3 mb-3">
          <h4 className="font-pixel text-[10px] text-neon-cyan/60 mb-2">
            CONNECTED TO TODAY&apos;S ISSUES
          </h4>
          <div className="space-y-1.5">
            {senator.connectedIssues.map((iss) => (
              <div
                key={iss.id}
                className="flex items-start gap-2 text-sm"
              >
                <span className="text-[10px] font-pixel text-neon-cyan/40 shrink-0 mt-0.5">
                  #{iss.rank}
                </span>
                <span className="text-matrix-green/70 leading-snug">{iss.title}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <Link
        href={`/politicians/${senator.id}`}
        className="inline-block font-pixel text-[10px] text-neon-cyan border border-neon-cyan/30 px-3 py-1.5 hover:bg-neon-cyan/10 transition-colors"
      >
        VIEW FULL SCORECARD →
      </Link>

      <ContactScript
        name={senator.name}
        stateName={STATES.find((s) => s.code === senator.state)?.name || senator.state}
        phone={senator.officePhone}
        contactFormUrl={senator.contactFormUrl}
      />
    </div>
  );
}

function RepCard({ rep }: { rep: MyRepRep }) {
  const s = rep.scores;

  return (
    <div
      className={`terminal-window border ${PARTY_BORDER[rep.party]} ${PARTY_BG[rep.party]} p-5`}
    >
      <div className="flex items-start justify-between gap-3 mb-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span
              className={`font-pixel text-xs px-1.5 py-0.5 border ${PARTY_BORDER[rep.party]} ${PARTY_COLORS[rep.party]}`}
            >
              {rep.party}
            </span>
            <span className="text-matrix-green/40 text-[10px] font-pixel">
              {rep.state}-{rep.district}
            </span>
            {rep.yearsInOffice > 0 && (
              <span className="text-matrix-green/50 text-[10px] font-pixel">
                {rep.yearsInOffice}yr{rep.yearsInOffice !== 1 ? "s" : ""}
              </span>
            )}
          </div>
          <h3 className="font-pixel text-base sm:text-lg text-matrix-green leading-snug">
            {rep.name}
          </h3>
          <div className="text-[10px] text-neon-cyan/40 font-pixel mt-0.5">
            DISTRICT {rep.district}
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className="font-pixel text-2xl text-matrix-green">{Math.round(s.overall)}</div>
          <div className="text-[10px] text-matrix-green/40 font-pixel">OVERALL</div>
        </div>
      </div>

      <div className="space-y-1.5 mb-4">
        <ScoreBar label={SCORE_TERMS["fundingIndependence"].shortLabel} value={s.fundingIndependence} />
        <ScoreBar label={SCORE_TERMS["independentVoting"].shortLabel} value={s.independentVoting} />
        <ScoreBar label={SCORE_TERMS["fundingDiversity"].shortLabel} value={s.fundingDiversity} />
        <ScoreBar label={SCORE_TERMS["legislativeEffectiveness"].shortLabel} value={s.legislativeEffectiveness} />
      </div>

      {rep.connectedIssues.length > 0 && (
        <div className="border-t border-matrix-green/10 pt-3 mb-3">
          <h4 className="font-pixel text-[10px] text-neon-cyan/60 mb-2">
            CONNECTED TO TODAY&apos;S ISSUES
          </h4>
          <div className="space-y-1.5">
            {rep.connectedIssues.map((iss) => (
              <div key={iss.id} className="flex items-start gap-2 text-sm">
                <span className="text-[10px] font-pixel text-neon-cyan/40 shrink-0 mt-0.5">
                  #{iss.rank}
                </span>
                <span className="text-matrix-green/70 leading-snug">{iss.title}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <Link
        href={`/politicians/${rep.id}`}
        className="inline-block font-pixel text-[10px] text-neon-cyan border border-neon-cyan/30 px-3 py-1.5 hover:bg-neon-cyan/10 transition-colors"
      >
        VIEW FULL SCORECARD →
      </Link>

      <ContactScript
        name={rep.name}
        stateName={STATES.find((s) => s.code === rep.state)?.name || rep.state}
        phone={rep.officePhone}
        contactFormUrl={rep.contactFormUrl}
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

  const myRepIds = useMemo(() => {
    if (!data) return new Set<string>();
    const ids = new Set<string>();
    data.senators.forEach((s) => ids.add(s.id));
    (data.representatives || []).forEach((r) => ids.add(r.id));
    return ids;
  }, [data]);

  const repIssues = useMemo(() => {
    if (myRepIds.size === 0 || activeIssues.length === 0) return [];
    return activeIssues
      .filter((iss) =>
        iss.relatedSenators?.some((s) => myRepIds.has(s.id))
      )
      .slice(0, 3);
  }, [issues, myRepIds]);

  if (!userState) {
    return (
      <div className="terminal-window max-w-md mx-auto p-8 text-center space-y-5">
        <div className="font-pixel text-sm text-neon-cyan/80 mb-2">
          SELECT YOUR STATE
        </div>
        <p className="text-matrix-green/60 text-sm leading-relaxed">
          Choose your state to see your senators, their scores, and how they
          connect to today&apos;s top issues.
        </p>
        <select
          value=""
          onChange={(e) => setUserState(e.target.value || null)}
          className="w-full bg-matrix-dark-green/50 border border-matrix-green/30 text-matrix-green
                     px-4 py-3 font-pixel text-sm focus:outline-none focus:border-neon-cyan/50"
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
        <div className="text-neon-cyan animate-pulse font-pixel text-sm">
          {">"} LOADING YOUR REPRESENTATIVES...
        </div>
      </div>
    );
  }

  if (fetchError) {
    return (
      <div className="terminal-window max-w-lg mx-auto p-8 text-center space-y-4" role="alert">
        <div className="font-pixel text-sm text-red-400">CONNECTION ERROR</div>
        <p className="text-matrix-green/50 text-sm">Could not load representative data.</p>
        <button
          onClick={() => loadReps(userState)}
          className="text-neon-cyan font-pixel text-sm border border-neon-cyan/30 px-4 py-2 hover:bg-neon-cyan/10 transition-colors"
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
          <h2 className="font-pixel text-sm sm:text-base text-matrix-green">
            YOUR REPRESENTATIVES — {stateName.toUpperCase()}
          </h2>
          {data?.issueDate && (
            <div className="text-[10px] text-matrix-green/40 font-pixel mt-1">
              ISSUES FROM {data.issueDate}
            </div>
          )}
        </div>
        <button
          onClick={() => setUserState(null)}
          className="font-pixel text-[10px] text-matrix-green/40 hover:text-matrix-green border border-matrix-green/20 px-2 py-1 transition-colors"
          aria-label="Change state"
        >
          CHANGE STATE
        </button>
      </div>

      {repIssues.length > 0 && (
        <div className="terminal-window border border-neon-pink/20 bg-neon-pink/5 p-4 space-y-3">
          <div className="font-pixel text-xs text-neon-pink/70">
            {">"} YOUR REPS IN THE NEWS
          </div>
          <div className="space-y-2">
            {repIssues.map((iss) => (
              <Link
                key={iss.id}
                href={`/action?issue=${iss.id}`}
                className="flex items-start gap-3 group hover:bg-neon-pink/10 transition-colors p-2 -mx-2"
              >
                <span className="text-[10px] font-pixel text-neon-pink/40 shrink-0 mt-0.5">
                  #{iss.rank}
                </span>
                <span className="text-sm text-matrix-green/80 group-hover:text-matrix-green leading-snug">
                  {iss.title}
                </span>
                <span className="text-[10px] font-pixel text-neon-pink/40 shrink-0 ml-auto mt-0.5" aria-hidden="true">
                  →
                </span>
              </Link>
            ))}
          </div>
        </div>
      )}

      {data && (data.senators.length > 0 || (data.representatives && data.representatives.length > 0)) ? (
        <div className="space-y-6">
          {data.senators.length > 0 && (
            <div className="space-y-4">
              <div className="font-pixel text-xs text-neon-pink/60">
                {">"} SENATORS
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {data.senators.map((senator) => (
                  <SenatorCard key={senator.id} senator={senator} />
                ))}
              </div>
            </div>
          )}

          {data.representatives && data.representatives.length > 0 && (
            <div className="space-y-4">
              <div className="font-pixel text-xs text-neon-pink/60">
                {">"} HOUSE REPRESENTATIVES
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {data.representatives.map((rep) => (
                  <RepCard key={rep.id} rep={rep} />
                ))}
              </div>
            </div>
          )}

          {/* YOUR REPS IN THE NEWS — shown when parent passes pre-fetched issues */}
          {(() => {
            if (!issues || issues.length === 0) return null;
            const repIssueIds = new Set<number>([
              ...data.senators.flatMap((s) => s.connectedIssues.map((i) => i.id)),
              ...(data.representatives ?? []).flatMap((r) => r.connectedIssues.map((i) => i.id)),
            ]);
            const matched = issues.filter((iss) => repIssueIds.has(iss.id));
            if (matched.length === 0) return null;
            return (
              <div className="space-y-4">
                <div className="font-pixel text-xs text-amber-400/60">
                  {">"} YOUR REPS IN THE NEWS
                </div>
                <div className="space-y-2">
                  {matched.map((iss) => (
                    <Link
                      key={iss.id}
                      href={`/action?issue=${iss.id}`}
                      className="block terminal-window border border-amber-400/20 p-3 hover:border-amber-400/40 transition-colors"
                    >
                      <div className="text-[10px] font-pixel text-amber-400/50 mb-1">{iss.date}</div>
                      <div className="text-sm text-matrix-green/80 leading-snug">{iss.title}</div>
                    </Link>
                  ))}
                </div>
              </div>
            );
          })()}
        </div>
      ) : (
        <div className="terminal-window p-8 text-center">
          <div className="font-pixel text-sm text-amber-400/80">NO REPRESENTATIVE DATA</div>
          <p className="text-matrix-green/50 text-sm mt-2">
            Senator and representative data for {stateName} is not yet available. Run the pipeline
            to populate scores.
          </p>
        </div>
      )}
    </div>
  );
}
