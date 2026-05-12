"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { usePlainLanguage } from "@/context/PlainLanguageContext";
import { fetchMyReps } from "@/lib/api";
import { STATES } from "@/data/states";
import type { MyRepRep, MyRepSenator, MyRepsResponse } from "@/types/action";

const PARTY_COLORS: Record<string, string> = {
  D: "text-dem-blue",
  R: "text-rep-red",
  I: "text-ind-purple",
};

const PARTY_BORDER: Record<string, string> = {
  D: "border-dem-blue/30",
  R: "border-rep-red/30",
  I: "border-ind-purple/30",
};

const PARTY_BG: Record<string, string> = {
  D: "bg-dem-blue/5",
  R: "bg-rep-red/5",
  I: "bg-ind-purple/5",
};

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
  const { terms } = usePlainLanguage();

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
              <span className="text-matrix-green/30 text-[10px] font-pixel">
                {senator.yearsInOffice}yr{senator.yearsInOffice !== 1 ? "s" : ""}
              </span>
            )}
          </div>
          <h3 className="font-pixel text-base sm:text-lg text-matrix-green leading-snug">
            {senator.name}
          </h3>
          {senator.punkNickname && (
            <div className="text-[10px] text-matrix-green/30 font-pixel mt-0.5 italic">
              &quot;{senator.punkNickname}&quot;
            </div>
          )}
        </div>
        <div className="text-right shrink-0">
          <div className="font-pixel text-2xl text-matrix-green">{Math.round(s.overall)}</div>
          <div className="text-[10px] text-matrix-green/40 font-pixel">OVERALL</div>
        </div>
      </div>

      <div className="space-y-1.5 mb-4">
        <ScoreBar label={terms("fundingIndependence").shortLabel} value={s.fundingIndependence} />
        <ScoreBar label={terms("promisePersistence").shortLabel} value={s.promisePersistence} />
        <ScoreBar label={terms("independentVoting").shortLabel} value={s.independentVoting} />
        <ScoreBar label={terms("fundingDiversity").shortLabel} value={s.fundingDiversity} />
        <ScoreBar label={terms("legislativeEffectiveness").shortLabel} value={s.legislativeEffectiveness} />
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
        href={`/scorecard?branch=senate&state=${senator.state}&senator=${senator.id}`}
        className="inline-block font-pixel text-[10px] text-neon-cyan border border-neon-cyan/30 px-3 py-1.5 hover:bg-neon-cyan/10 transition-colors"
      >
        VIEW FULL SCORECARD →
      </Link>
    </div>
  );
}

function RepCard({ rep }: { rep: MyRepRep }) {
  const s = rep.scores;
  const { terms } = usePlainLanguage();

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
              <span className="text-matrix-green/30 text-[10px] font-pixel">
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
        <ScoreBar label={terms("fundingIndependence").shortLabel} value={s.fundingIndependence} />
        <ScoreBar label={terms("promisePersistence").shortLabel} value={s.promisePersistence} />
        <ScoreBar label={terms("independentVoting").shortLabel} value={s.independentVoting} />
        <ScoreBar label={terms("fundingDiversity").shortLabel} value={s.fundingDiversity} />
        <ScoreBar label={terms("legislativeEffectiveness").shortLabel} value={s.legislativeEffectiveness} />
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
        href={`/scorecard?branch=house&state=${rep.state}&rep=${rep.id}`}
        className="inline-block font-pixel text-[10px] text-neon-cyan border border-neon-cyan/30 px-3 py-1.5 hover:bg-neon-cyan/10 transition-colors"
      >
        VIEW FULL SCORECARD →
      </Link>
    </div>
  );
}

export default function MyRepsTab({
  userState,
  setUserState,
}: {
  userState: string | null;
  setUserState: (s: string | null) => void;
}) {
  const [data, setData] = useState<MyRepsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [fetchError, setFetchError] = useState(false);

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
