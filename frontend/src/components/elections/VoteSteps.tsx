import type { VoteStep, VoteTask, VoterCalendar } from "@/types/election";
import { safeHref } from "@/lib/formatting";
import { INLINE_LINK } from "@/lib/controlStyles";
import { countdownText } from "@/components/elections/VoterCalendar";
import BoxGrid from "@/components/shared/BoxGrid";
import RecordBox from "@/components/shared/RecordBox";

/** The verb on each step's button. No two share a label (WCAG 2.4.6). */
export const STEP_LABEL: Record<VoteTask, string> = {
  check_registration: "Check your registration",
  register: "Register to vote",
  sample_ballot: "See your sample ballot",
  polling_place: "Find your polling place",
  mail_ballot: "Vote early or by mail",
};

/** A step's label: the task's verb, except a sample ballot in a state whose
 *  election authority publishes none statewide, which says who does and
 *  why the button opens a directory of local offices (backend
 *  `sample_ballot_office`). */
export function stepLabel(step: VoteStep): string {
  if (step.task === "sample_ballot" && !step.isStateSpecific && step.localOffice) {
    return `Your ${step.localOffice} publishes sample ballots: find your ${step.localOffice} election office`;
  }
  return STEP_LABEL[step.task];
}

/** Where the step goes, printed under its label: the host, then whose
 *  page it is ("pa.gov · Pennsylvania Department of State"), or, for a
 *  fallback, the federal or national directory it is. */
function destination(step: VoteStep): string {
  if (step.task === "sample_ballot" && !step.isStateSpecific && !step.localOffice) {
    return `your county election office, through ${step.sourceName ?? "USAGov"}`;
  }
  return [step.host, step.sourceName].filter(Boolean).join(" · ");
}

function deadlineText(step: VoteStep): string | null {
  const d = step.deadline;
  if (!d) return null;
  const left =
    d.status === "open" ? countdownText({ status: "open", countdown: d.countdown }) : null;
  return left ? `${d.sentence} · ${left}` : d.sentence;
}

/** A step whose site turns away the nightly link check: said, with the
 *  national fallback beside it. Never worked around (AGENTS.md section 7). */
function Unconfirmed({ step, className }: { step: VoteStep; className: string }) {
  const fallbackHref = step.fallback ? safeHref(step.fallback.url) : null;
  if (!step.unconfirmed || !fallbackHref || !step.fallback) return null;
  return (
    <p className={`${className} wrap-anywhere`}>
      This site blocks automated checks, so we can&apos;t confirm it&apos;s up. If it doesn&apos;t
      open, use{" "}
      <a
        href={fallbackHref}
        target="_blank"
        rel="noopener noreferrer"
        className={INLINE_LINK}
        aria-label={`${stepLabel(step)} through ${step.fallback.sourceName} (opens in new tab)`}
      >
        {step.fallback.host} ↗
      </a>
    </p>
  );
}

function stepAriaLabel(step: VoteStep, deadline: string | null): string {
  return `${stepLabel(step)}: ${destination(step)}${step.unconfirmed ? ", unconfirmed" : ""}${deadline ? `. ${deadline}` : ""} (opens in new tab)`;
}

function Step({ step, n }: { step: VoteStep; n: number }) {
  const label = stepLabel(step);
  const href = safeHref(step.url ?? "");
  const deadline = deadlineText(step);
  const closed = step.deadline?.status === "closed";
  const where = destination(step);
  const body = (
    <>
      <span aria-hidden="true" className="w-6 shrink-0 font-mono text-lg text-ink-lo">
        {n}
      </span>
      <span className="min-w-0 flex-1 wrap-anywhere">
        <span className="block text-[17px] font-semibold leading-snug text-ink-hi">{label}</span>
        {where && <span className="block text-sm text-ink-lo">{where}</span>}
        {deadline && (
          <span
            className={`block text-sm font-semibold ${closed ? "text-ink-lo" : "text-signal-amber"}`}
          >
            {deadline}
          </span>
        )}
      </span>
      {href && (
        <span aria-hidden="true" className="shrink-0 text-ink-lo">
          ↗
        </span>
      )}
    </>
  );
  return (
    <li className="min-w-0 border border-white/15 bg-surface">
      {href ? (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={stepAriaLabel(step, deadline)}
          className="flex min-h-[52px] items-start gap-3 px-4 py-3 hover:bg-white/[0.04] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
        >
          {body}
        </a>
      ) : (
        // A state with no voter registration: said, with no link to follow.
        <div className="flex min-h-[52px] items-start gap-3 px-4 py-3">{body}</div>
      )}
      <Unconfirmed
        step={step}
        className="border-t border-white/[0.09] px-4 py-2 pl-[52px] text-xs text-ink-lo"
      />
    </li>
  );
}

/**
 * Above the ballot: the state's official sample-ballot lookup as the one
 * button, then the next voter deadline in one line with a jump to every
 * step and the full calendar, after the contests. The ballot lookup leads
 * because a reader who couldn't find it clicked registration instead
 * (#1123); registration is left out here so the two can't be confused
 * again (it is in the steps and the deadline line). Its link, liveness
 * wording and fallback are the steps' own. Every date stays quoted with its
 * rule and source in the calendar below (AGENTS.md section 7).
 */
export function VoteStepsLead({
  calendar,
  steps,
}: {
  calendar: VoterCalendar | null | undefined;
  steps: VoteStep[] | null | undefined;
}) {
  const hasSteps = !!steps?.length;
  const ballotStep = steps?.find((s) => s.task === "sample_ballot") ?? null;
  const ballotHref = ballotStep ? safeHref(ballotStep.url ?? "") : null;
  const read = calendar && !calendar.unread ? calendar : null;
  const next = read?.next ?? null;
  const passed = !!read && !next && read.items.length > 0;
  if (!hasSteps && !calendar) return null;
  const left = next ? countdownText(next) : null;
  const soon =
    next?.status === "open" && (next.countdown === "today" || next.countdown === "tomorrow");
  const source = next?.source ? safeHref(next.source.url) : null;
  const jump = (
    <a
      href={hasSteps ? "#vote-steps" : "#voter-deadlines"}
      className="inline-flex min-h-6 items-center text-sm font-semibold text-ink-hi underline underline-offset-2 hover:text-phos"
    >
      {hasSteps ? "Your vote, step by step, and every deadline" : "Every voter deadline"}
      <span aria-hidden="true">&nbsp;↓</span>
    </a>
  );
  return (
    <div data-testid="vote-steps-lead" className="mb-4">
      {ballotStep && ballotHref && (
        <div className="mb-2">
          <a
            href={ballotHref}
            target="_blank"
            rel="noopener noreferrer"
            data-testid="ballot-lookup"
            aria-label={stepAriaLabel(ballotStep, null)}
            className="flex min-h-11 w-full items-center gap-3 bg-phos px-4 py-2 text-surface-base hover:bg-phos-mid focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 sm:w-fit"
          >
            <span className="min-w-0 flex-1 wrap-anywhere">
              <span className="block font-semibold">{stepLabel(ballotStep)}</span>
              <span className="block text-sm">Opens {destination(ballotStep)}</span>
            </span>
            <span aria-hidden="true">↗</span>
          </a>
          <Unconfirmed step={ballotStep} className="mt-1 text-xs text-ink-lo" />
        </div>
      )}
      {/* The next deadline as a box: the calendar's own sentence, the
          rule behind "More" with its publisher, and the days left as the
          figure (the backend's count, on the state's clock). */}
      {next || passed ? (
        <BoxGrid columns={1}>
          <RecordBox
            kicker={soon ? `Next deadline · ${left}` : "Next deadline"}
            mark={soon ? "!" : undefined}
            tone={next ? "deadline" : "neutral"}
            title={next ? next.sentence : "Every voter deadline for this election has passed"}
            level={2}
            // Whose date it is, linked; the rule in the state's terms
            // behind "More", as the calendar row below gives it (not in
            // quotation marks: the calendar states the rule, it doesn't
            // quote it).
            dek={
              next ? (
                <span className="text-sm text-ink-lo">
                  {source ? (
                    <a href={source} rel="noopener noreferrer" className={INLINE_LINK}>
                      From {next.source.publisher ?? "the state"}
                      <span aria-hidden="true">&nbsp;↗</span>
                    </a>
                  ) : (
                    `From ${next.source?.publisher ?? "the state"}`
                  )}
                </span>
              ) : undefined
            }
            more={
              next?.rule
                ? { label: "the rule, in the state's terms", children: <p>{next.rule}</p> }
                : null
            }
            figure={
              next?.status === "open" && next.daysLeft != null
                ? { type: "countdown", closes: next.closesOn, daysLeft: next.daysLeft, caption: "" }
                : null
            }
          >
            {!soon && left && next?.status !== "open" && (
              <p className="mb-2 text-sm font-semibold text-signal-amber">{left}</p>
            )}
            {jump}
          </RecordBox>
        </BoxGrid>
      ) : (
        <p className="text-sm">{jump}</p>
      )}
    </div>
  );
}

/**
 * "Your vote, step by step": one official link per voting task, in the
 * order a voter does them, each naming where it goes and carrying its
 * deadline. Every link is checked nightly (backend
 * pipeline/fetch/ballot_lookup.py); nothing about the visitor is asked or
 * sent (AGENTS.md section 8).
 */
export default function VoteSteps({ steps, stateName }: { steps: VoteStep[]; stateName: string }) {
  if (steps.length === 0) return null;
  return (
    <section id="vote-steps" aria-labelledby="vote-steps-heading" className="mb-6 min-w-0">
      <h2
        id="vote-steps-heading"
        className="border-t-[3px] border-ink-hi pt-2 font-display text-xl font-semibold text-ink-hi"
      >
        Your vote, step by step
      </h2>
      <p className="mb-3 mt-1 text-sm text-ink-lo">
        Each button opens an official site for {stateName}, named under it.
      </p>
      <ol className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {steps.map((s, i) => (
          <Step key={s.task} step={s} n={i + 1} />
        ))}
      </ol>
      <p className="mt-3 text-sm text-ink-lo">
        This page is research, not your official ballot. Your sample ballot also lists county, city
        and local questions this page leaves out.
      </p>
    </section>
  );
}
