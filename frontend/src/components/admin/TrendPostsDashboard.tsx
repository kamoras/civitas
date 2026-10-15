"use client";

import { useEffect, useState } from "react";
import { fetchAdminTrendPosts } from "@/lib/api";
import type { PartyCounts, TrendPartyAudit, TrendPostsAudit } from "@/types/trendPosts";
import { formatTime } from "./format";
import { Panel, StatTile } from "./widgets";

const DAYS = 30;
const NOT_MEASURED = "—";

/** A score, share or count the backend may not have measured (null). */
export function formatMeasure(value: number | null | undefined, digits = 2): string {
  return value == null ? NOT_MEASURED : value.toFixed(digits);
}

export function formatShare(share: number | null): string {
  return share == null ? NOT_MEASURED : `${Math.round(share * 100)}% D`;
}

export function formatPValue(p: number | null): string {
  if (p == null) return NOT_MEASURED;
  return p < 0.001 ? "<0.001" : p.toFixed(3);
}

/** What the party audit says, in the order an operator needs it: too few
 *  leaning posts to test, nothing to test against, a divergence, or none. */
export function auditStatus(audit: TrendPartyAudit): { label: string; tone: string } {
  const leaning = audit.posted.D + audit.posted.R;
  if (leaning < audit.minPosted) {
    return {
      label: `Too few posts (need ${audit.minPosted} leaning, have ${leaning})`,
      tone: "text-ink-lo",
    };
  }
  if (audit.pValue == null) return { label: "Not measured", tone: "text-ink-lo" };
  return audit.diverges
    ? { label: `Diverges (p < ${audit.alpha})`, tone: "text-signal-red" }
    : { label: "Within expectation", tone: "text-ink-hi" };
}

function counts(c: PartyCounts): string {
  return `D ${c.D} · R ${c.R} · neither ${c.none}`;
}

function AuditBlock({ title, audit }: { title: string; audit: TrendPartyAudit }) {
  const status = auditStatus(audit);
  return (
    <div className="space-y-3">
      <h3 className="text-xs font-mono text-ink-lo tracking-wider">{title}</h3>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Status" value={status.label} tone={status.tone} />
        <StatTile
          label="Eligible pool"
          value={formatShare(audit.poolDemocraticShare)}
          note={counts(audit.pool)}
        />
        <StatTile
          label="Posted"
          value={formatShare(audit.postedDemocraticShare)}
          note={counts(audit.posted)}
        />
        <StatTile
          label="Binomial p-value"
          value={formatPValue(audit.pValue)}
          note={`alerts below ${audit.alpha} with ${audit.minPosted}+ leaning posts`}
        />
      </div>
    </div>
  );
}

const th = "py-1 pr-3 font-normal";

/** The trend posts' audit (backend analyze/trend_poster.py): whether what
 *  was posted leans differently from the eligible pool, how long trends
 *  stay listed, the image-vs-card trial, and every candidate's decision. */
export function TrendPostsDashboard({ token }: { token: string }) {
  const [audit, setAudit] = useState<TrendPostsAudit | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchAdminTrendPosts(token, DAYS)
      .then((a) => {
        if (cancelled) return;
        setAudit(a);
        setFailed(false);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  if (failed) {
    return (
      <p role="alert" className="text-xs font-mono text-signal-red">
        The trend posts audit couldn&apos;t be loaded.
      </p>
    );
  }
  if (!audit) return <p className="text-xs font-mono text-ink-min">Loading…</p>;

  const life = audit.lifetimes;
  const decisions = Object.entries(audit.decisions).sort((a, b) => b[1] - a[1]);

  return (
    <div className="space-y-6">
      <Panel title="Party balance">
        <div className="space-y-6 p-4">
          <AuditBlock title={`LAST ${DAYS} DAYS`} audit={audit.partyAudit} />
          <AuditBlock title="ALL TIME" audit={audit.partyAuditAllTime} />
          <p className="text-xs font-mono text-ink-min">
            Each candidate counts once, by the lean of its named members and sponsors. Selection is
            party-blind; the test is the posted Democratic count against the pool&apos;s share.
          </p>
        </div>
      </Panel>

      <Panel title="Trend lifetimes">
        <div className="grid grid-cols-2 gap-3 p-4 lg:grid-cols-4">
          <StatTile
            label="Median"
            value={life.median == null ? NOT_MEASURED : `${life.median} h`}
            note="from first listed to last seen"
          />
          <StatTile
            label="Quartiles"
            value={
              life.p25 == null || life.p75 == null ? NOT_MEASURED : `${life.p25}–${life.p75} h`
            }
          />
          <StatTile label="Topics ended" value={life.ended.toLocaleString()} />
          <StatTile label="Topics seen" value={life.topics.toLocaleString()} />
        </div>
      </Panel>

      <Panel title="Image vs link card">
        <div className="overflow-x-auto p-4">
          <table className="w-full text-xs font-mono">
            <caption className="sr-only">
              Trend posts sent to Bluesky by what they carried, with engagement
            </caption>
            <thead>
              <tr className="text-ink-lo border-b border-white/[0.07]">
                <th scope="col" className={`text-left ${th}`}>
                  CARRIED
                </th>
                <th scope="col" className={`text-right ${th}`}>
                  POSTS
                </th>
                <th scope="col" className={`text-right ${th}`}>
                  COUNTS READ
                </th>
                <th scope="col" className={`text-right ${th}`}>
                  MEAN LIKES
                </th>
                <th scope="col" className={`text-right ${th}`}>
                  MEDIAN LIKES
                </th>
                <th scope="col" className="text-right py-1 font-normal">
                  MEAN REPOSTS
                </th>
              </tr>
            </thead>
            <tbody>
              {audit.embedTrial.map((r) => (
                <tr key={r.embedKind} className="border-b border-white/[0.04]">
                  <th scope="row" className="text-left py-1 pr-3 font-normal text-ink">
                    {r.embedKind}
                  </th>
                  <td className="text-right py-1 pr-3 tabular-nums text-ink-hi">{r.posts}</td>
                  <td className="text-right py-1 pr-3 tabular-nums text-ink-lo">{r.read}</td>
                  <td className="text-right py-1 pr-3 tabular-nums text-ink-hi">
                    {formatMeasure(r.meanLikes)}
                  </td>
                  <td className="text-right py-1 pr-3 tabular-nums text-ink-hi">
                    {formatMeasure(r.medianLikes, 1)}
                  </td>
                  <td className="text-right py-1 tabular-nums text-ink-hi">
                    {formatMeasure(r.meanReposts)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel title="Decisions">
        <div className="p-4">
          {decisions.length === 0 ? (
            <p className="text-xs font-mono text-ink-min">No candidates yet.</p>
          ) : (
            <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              {decisions.map(([decision, n]) => (
                <div key={decision} className="border border-white/[0.07] p-3 min-w-0">
                  <dt className="text-xs font-mono text-ink-lo tracking-wider">{decision}</dt>
                  <dd className="mt-1 font-mono text-xl text-ink-hi">{n.toLocaleString()}</dd>
                </div>
              ))}
            </dl>
          )}
        </div>
      </Panel>

      <Panel title="Candidates">
        {audit.candidates.length === 0 ? (
          <p className="p-4 text-xs font-mono text-ink-min">No candidates yet.</p>
        ) : (
          <div className="overflow-x-auto p-4">
            <table className="w-full text-xs font-mono">
              <caption className="sr-only">
                Trend candidates, newest decision first, with match, record, parties and scores
              </caption>
              <thead>
                <tr className="text-ink-lo border-b border-white/[0.07] align-bottom">
                  <th scope="col" className={`text-left ${th}`}>
                    TREND
                  </th>
                  <th scope="col" className={`text-right ${th}`}>
                    RANK
                  </th>
                  <th scope="col" className={`text-left ${th}`}>
                    DECISION
                  </th>
                  <th scope="col" className={`text-left ${th}`}>
                    REASON
                  </th>
                  <th scope="col" className={`text-left ${th}`}>
                    MATCH
                  </th>
                  <th scope="col" className={`text-left ${th}`}>
                    RECORD
                  </th>
                  <th scope="col" className={`text-left ${th}`}>
                    PARTIES
                  </th>
                  <th scope="col" className={`text-right ${th}`}>
                    RELEVANCE
                  </th>
                  <th scope="col" className={`text-right ${th}`}>
                    TRAGEDY
                  </th>
                  <th scope="col" className={`text-right ${th}`}>
                    POL. VIOLENCE
                  </th>
                  <th scope="col" className="text-right py-1 font-normal">
                    LOADED
                  </th>
                </tr>
              </thead>
              <tbody>
                {audit.candidates.map((c) => (
                  <tr
                    key={`${c.topic}-${c.decidedAt}`}
                    className="border-b border-white/[0.04] align-top"
                  >
                    <th scope="row" className="text-left py-1 pr-3 font-normal text-ink">
                      {c.name}
                      <div className="text-ink-min">{formatTime(c.decidedAt)}</div>
                    </th>
                    <td className="text-right py-1 pr-3 tabular-nums text-ink-hi">
                      {c.rank ?? NOT_MEASURED}
                    </td>
                    <td className="py-1 pr-3 text-ink-hi">{c.decision}</td>
                    <td className="py-1 pr-3 text-ink-lo">
                      {c.reason ?? NOT_MEASURED}
                      {c.neutrality.length > 0 && (
                        <div className="text-signal-amber">{c.neutrality.join(", ")}</div>
                      )}
                    </td>
                    <td className="py-1 pr-3 text-ink-lo">
                      {c.matchType ?? NOT_MEASURED}
                      {c.matchType && ` ${formatMeasure(c.matchStrength)}`}
                    </td>
                    <td className="py-1 pr-3">
                      {c.recordUrl ? (
                        <a
                          href={c.recordUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-signal-cyan underline underline-offset-2"
                        >
                          {c.recordKey ?? c.recordKind ?? "record"}
                        </a>
                      ) : (
                        <span className="text-ink-min">{NOT_MEASURED}</span>
                      )}
                      {c.recordDate && <div className="text-ink-min">{c.recordDate}</div>}
                    </td>
                    <td className="py-1 pr-3 text-ink-lo">
                      <div>named: {c.namedParties.join(" ") || NOT_MEASURED}</div>
                      <div>sponsors: {c.sponsorParties.join(" ") || NOT_MEASURED}</div>
                    </td>
                    <td className="text-right py-1 pr-3 tabular-nums">
                      {formatMeasure(c.relevance)}
                    </td>
                    <td className="text-right py-1 pr-3 tabular-nums">
                      {formatMeasure(c.tragedy)}
                    </td>
                    <td className="text-right py-1 pr-3 tabular-nums">
                      {formatMeasure(c.politicalViolence)}
                    </td>
                    <td className="text-right py-1 tabular-nums">{formatMeasure(c.loaded)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
