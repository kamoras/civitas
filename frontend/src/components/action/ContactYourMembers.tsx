"use client";

import { useId, useMemo, useState } from "react";
import Link from "next/link";
import {
  fetchHouseDistrictsByCounty,
  fetchIssueMembers,
  sendActionTap,
  type ActionTapKind,
} from "@/lib/api";
import { useAsyncData } from "@/hooks/useAsyncData";
import { CopyStatus, useCopyFeedback } from "@/hooks/useCopyFeedback";
import { countyFinderStatus, countyNames, finderSuggestions } from "@/lib/countyFinder";
import { districtName, matchesDistrictQuery, placeDistrictSummary } from "@/lib/elections";
import { formatUtcDate, safeHref } from "@/lib/formatting";
import { PARTY_COLORS } from "@/lib/partyStyles";
import { boxedControl } from "@/lib/controlStyles";
import { STATE_CODES, STATE_NAMES } from "@/lib/stateCodes";
import { useYourState } from "@/hooks/useYourState";
import { rollCallHref } from "@/lib/congress";
import type { ActionIssue, IssueMember, IssueVote } from "@/types/action";

/**
 * "Call your members about this", on the issue itself.
 *
 * Before 2026-10-14 the only way from an issue to a phone number was the
 * directory: pick a state there, open a member's page, find the number —
 * five taps across two page loads. Here a reader picks a state from a list
 * (and, for the House, types a city, town or county the page already knows
 * the districts of) and gets each office's phone and contact form, how each
 * member voted on the issue's roll calls, and a call script to read from.
 *
 * Nothing about the reader is asked for or kept (AGENTS.md section 8): the
 * state is an explicit, unremembered pick from a list, sent in the URL to
 * load that state's members, and the town filter runs in the browser over
 * the district lists. Taps are counted per issue and kind only (ActionTap).
 */

// The site's bracketed command style ("[ ROLL CALL ]"), at a 32px touch target.
const COMMAND =
  "inline-flex min-h-[32px] items-center font-mono text-xs tracking-[0.12em] text-phos-mid hover:text-phos";

const STATES_BY_NAME = [...STATE_CODES].sort((a, b) =>
  STATE_NAMES[a].localeCompare(STATE_NAMES[b])
);

export type Position = "ask" | "support" | "oppose";

function title(m: { chamber: string }) {
  return m.chamber === "house" ? "Rep." : "Sen.";
}

/** What the call is about: the bill the issue names, or else its quoted
 *  summary with the outlet that wrote it. */
export function scriptSubject(issue: ActionIssue): { text: string; bill: boolean } {
  const bill = issue.relatedBills?.[0];
  if (bill) return { text: bill.name, bill: true };
  const outlet = issue.summarySource || issue.sourceNames?.[0];
  return {
    text: `this report${outlet ? ` from ${outlet}` : ""}: "${issue.summary.replace(/\.$/, "")}"`,
    bill: false,
  };
}

/**
 * A fixed call script: who is calling, about what, and what they want to
 * know or ask for. Every word is this template's or the record's (a bill
 * number, a quoted line and its outlet, the member's name). Support and
 * oppose are offered only for a bill, the one thing a member can vote yes or
 * no on, and are worded alike; the reader's own name and town stay
 * bracketed for them to fill in.
 */
export function callScript(
  issue: ActionIssue,
  member: { name: string; chamber: string },
  stateName: string,
  position: Position
): string {
  const subject = scriptSubject(issue);
  const who = `${title(member)} ${member.name}`;
  const ask =
    position === "support"
      ? `I support it, and I ask ${who} to vote yes.`
      : position === "oppose"
        ? `I oppose it, and I ask ${who} to vote no.`
        : `I would like to know where ${who} stands on it.`;
  return [
    `Hello. My name is [your name], and I live in [your town], ${stateName}.`,
    `I am calling about ${subject.text}.`,
    ask,
    "Thank you.",
  ].join("\n");
}

function tapper(issue: ActionIssue, kind: ActionTapKind) {
  return () => sendActionTap(issue.publicId, kind);
}

function Script({
  issue,
  member,
  stateName,
}: {
  issue: ActionIssue;
  member: IssueMember;
  stateName: string;
}) {
  const [position, setPosition] = useState<Position>("ask");
  const [copied, copy, feedback] = useCopyFeedback(1500, {
    copied: "Call script copied.",
    failed: "This browser wouldn't copy it. Select the script and copy it instead.",
  });
  const text = callScript(issue, member, stateName, position);
  const choices: [Position, string][] = scriptSubject(issue).bill
    ? [
        ["ask", "Ask"],
        ["support", "Support"],
        ["oppose", "Oppose"],
      ]
    : [];
  return (
    <div className="mt-2 border-l border-white/15 pl-3">
      {choices.length > 0 && (
        <div role="group" aria-label="Your position" className="mb-2 flex gap-2">
          {choices.map(([value, label]) => (
            <button
              key={value}
              type="button"
              aria-pressed={position === value}
              onClick={() => setPosition(value)}
              className={`min-h-[32px] min-w-[5.5rem] border px-2 font-mono text-xs uppercase tracking-[0.12em] ${boxedControl(position === value)}`}
            >
              {label}
            </button>
          ))}
        </div>
      )}
      <p className="whitespace-pre-line font-display text-[15px] leading-relaxed text-ink">
        {text}
      </p>
      <div className="mt-1 flex flex-wrap items-center gap-x-4">
        <button
          type="button"
          className={COMMAND}
          aria-label={`Copy the call script for ${title(member)} ${member.name}`}
          onClick={() => {
            sendActionTap(issue.publicId, "copy");
            copy(text);
          }}
        >
          {copied ? "[ COPIED ]" : "[ COPY ]"}
        </button>
        <CopyStatus feedback={feedback} />
      </div>
      <p className="mt-1 text-xs text-ink-min">
        Fill in the bracketed parts. Offices count calls from their own state or district.
      </p>
    </div>
  );
}

function MemberRow({
  issue,
  member,
  votes,
  stateName,
}: {
  issue: ActionIssue;
  member: IssueMember;
  votes: IssueVote[];
  stateName: string;
}) {
  const [scriptOpen, setScriptOpen] = useState(false);
  const who = `${title(member)} ${member.name}`;
  const scriptId = useId();
  const record = votes.flatMap((v) => {
    const position = member.positions[`${v.chamber}-${v.congress}-${v.session}-${v.number}`];
    return position ? [{ v, position }] : [];
  });
  return (
    <li className="border-t border-white/[0.07] py-2.5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <span className="font-display text-[15px] text-ink-hi">
          <Link href={`/politicians/${member.id}`} className="underline-offset-4 hover:underline">
            {who}
          </Link>{" "}
          <span className={`font-mono text-xs ${PARTY_COLORS[member.party] ?? "text-ink-lo"}`}>
            {member.party}
          </span>
          {member.chamber === "house" && member.district != null && (
            <span className="font-mono text-xs text-ink-min">
              {" "}
              · {districtName(member.district)}
            </span>
          )}
        </span>
        <span className="flex flex-wrap gap-x-4">
          {member.officePhone && (
            <a
              href={`tel:${member.officePhone.replace(/[^0-9+]/g, "")}`}
              className={COMMAND}
              aria-label={`Call ${who}'s office, ${member.officePhone}`}
              onClick={tapper(issue, "call")}
            >
              [ CALL {member.officePhone} ]
            </a>
          )}
          {safeHref(member.contactFormUrl) && (
            <a
              href={safeHref(member.contactFormUrl)}
              target="_blank"
              rel="noopener noreferrer"
              className={COMMAND}
              aria-label={`Write to ${who} (opens the office's contact form)`}
              onClick={tapper(issue, "write")}
            >
              [ WRITE ↗ ]
            </a>
          )}
          <button
            type="button"
            className={COMMAND}
            aria-expanded={scriptOpen}
            aria-controls={scriptId}
            aria-label={`What to say to ${who}`}
            onClick={() => setScriptOpen((o) => !o)}
          >
            [ WHAT TO SAY ]
          </button>
        </span>
      </div>
      {record.map(({ v, position }) => {
        const what = [v.billLabel, v.question].filter(Boolean).join(" · ");
        return (
          <p key={v.number + v.chamber} className="mt-0.5 text-[13px] text-ink-lo">
            <span className="font-mono text-xs uppercase tracking-[0.08em] text-ink-hi">
              {position}
            </span>{" "}
            on{" "}
            <Link
              href={rollCallHref(v)}
              className="underline decoration-white/20 underline-offset-4"
            >
              {what}
            </Link>{" "}
            · {formatUtcDate(v.date, { month: "short", day: "numeric", year: "numeric" })} ·{" "}
            {v.result}
          </p>
        );
      })}
      <div id={scriptId}>
        {scriptOpen && <Script issue={issue} member={member} stateName={stateName} />}
      </div>
    </li>
  );
}

export default function ContactYourMembers({
  issue,
  afterNamed = false,
}: {
  issue: ActionIssue;
  /** Members the coverage names are listed above: these are the reader's own. */
  afterNamed?: boolean;
}) {
  // Starts from the state chosen in the header for this tab
  // (useYourState) and follows it until the reader picks here; a pick here
  // becomes the header's.
  const yours = useYourState();
  const [picked, setPicked] = useState<string | null>(null);
  const state = picked ?? yours.state;
  const setState = (next: string) => {
    setPicked(next);
    if (next) yours.setState(next);
  };
  const [place, setPlace] = useState("");
  const [showAllHouse, setShowAllHouse] = useState(false);
  const selectId = useId();
  const placeId = useId();
  const members = useAsyncData(
    `issue-members:${issue.publicId}:${state}`,
    state ? () => fetchIssueMembers(issue.publicId, state) : null
  );
  const lines = useAsyncData(
    `house-districts:${state}`,
    state && state !== "DC" ? () => fetchHouseDistrictsByCounty(state) : null
  ).data;

  const all = members.data?.members ?? [];
  const senators = all.filter((m) => m.chamber === "senate");
  const reps = all.filter((m) => m.chamber === "house");
  const finder = !!lines?.available && reps.length > 1;
  const matchedDistricts = useMemo(
    () =>
      finder && place.trim()
        ? lines!.districts.filter((d) =>
            matchesDistrictQuery(
              { district: null, areas: [...d.counties, ...(d.places ?? [])], candidates: [] },
              place
            )
          )
        : [],
    [finder, lines, place]
  );
  const shownReps =
    !finder || showAllHouse
      ? reps
      : reps.filter((r) => matchedDistricts.some((d) => d.district === r.district));
  const stateName = STATE_NAMES[state] ?? state;
  const status = finder
    ? countyFinderStatus(
        countyNames(lines),
        place,
        matchedDistricts.length,
        placeDistrictSummary(lines!.districts, place)
      )
    : null;
  const votes = members.data?.votes ?? [];

  return (
    <div>
      <p className="font-display text-[15px] leading-snug text-ink-hi">
        {afterNamed
          ? "Or contact your own senators and representative"
          : "Your senators and representative"}
      </p>
      <p className="mt-0.5 text-[13px] text-ink-lo">
        Pick your state for each office&apos;s phone and contact form
        {(issue.voteCount ?? 0) > 0 ? ", and how each member voted on this" : ""}. Your pick stays
        in this tab only.
      </p>
      <label htmlFor={selectId} className="sr-only">
        Your state
      </label>
      <select
        id={selectId}
        value={state}
        onChange={(e) => {
          // Only a known postal code becomes state: it goes into hrefs and
          // the members request.
          const picked = Object.hasOwn(STATE_NAMES, e.target.value) ? e.target.value : "";
          setState(picked);
          setPlace("");
          setShowAllHouse(false);
          if (picked) sendActionTap(issue.publicId, "pick");
        }}
        className="mt-2 min-h-[36px] w-full max-w-xs border border-white/15 bg-surface-base px-2 font-mono text-sm text-ink-hi outline-none focus:border-ink-lo"
      >
        <option value="">Pick your state…</option>
        {STATES_BY_NAME.map((code) => (
          <option key={code} value={code}>
            {STATE_NAMES[code]}
          </option>
        ))}
      </select>

      {state && members.loading && <p className="mt-2 text-[13px] text-ink-min">Loading…</p>}
      {state && members.error && (
        <p className="mt-2 text-[13px] text-signal-amber" role="alert">
          Couldn&apos;t load {stateName}&apos;s members. Try again, or use the{" "}
          <Link
            href={`/politicians?state=${encodeURIComponent(state)}`}
            className="underline underline-offset-2"
          >
            directory
          </Link>
          .
        </p>
      )}

      {members.data && (
        <>
          <ul className="mt-3">
            {senators.map((m) => (
              <MemberRow key={m.id} issue={issue} member={m} votes={votes} stateName={stateName} />
            ))}
          </ul>
          {finder && (
            <div className="mt-3">
              <label htmlFor={placeId} className="block text-[13px] text-ink-lo">
                {stateName} has {reps.length} representatives. Type your city, town or county to
                find yours:
              </label>
              <input
                id={placeId}
                type="search"
                list={`${placeId}-places`}
                value={place}
                onChange={(e) => setPlace(e.target.value)}
                placeholder="City, town or county"
                autoComplete="off"
                className="mt-1 min-h-[36px] w-full max-w-xs border border-white/15 bg-surface-base px-2 font-mono text-sm text-ink-hi placeholder:text-ink-min outline-none focus:border-ink-lo"
              />
              <datalist id={`${placeId}-places`}>
                {finderSuggestions(lines).map((n) => (
                  <option key={n} value={n} />
                ))}
              </datalist>
              {status && (
                <p className="mt-1 text-[13px] text-ink-lo" role="status">
                  {status}
                </p>
              )}
              {!showAllHouse && (
                <button type="button" className={COMMAND} onClick={() => setShowAllHouse(true)}>
                  [ ALL {reps.length} ]
                </button>
              )}
            </div>
          )}
          {shownReps.length > 0 && (
            <ul className="mt-1">
              {shownReps.map((m) => (
                <MemberRow
                  key={m.id}
                  issue={issue}
                  member={m}
                  votes={votes}
                  stateName={stateName}
                />
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
