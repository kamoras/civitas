import type { VoteStep, VoteTask } from "@/types/election";
import { safeHref } from "@/lib/formatting";
import { INLINE_LINK } from "@/lib/controlStyles";
import { countdownText } from "@/components/elections/VoterCalendar";

/** The verb on each step's button. No two share a label (WCAG 2.4.6). */
export const STEP_LABEL: Record<VoteTask, string> = {
  check_registration: "Check your registration",
  register: "Register to vote",
  sample_ballot: "See your sample ballot",
  polling_place: "Find your polling place",
  mail_ballot: "Vote early or by mail",
};

/** Where the step goes, printed under its label: the host, then whose
 *  page it is ("pa.gov · Pennsylvania Department of State"), or, for a
 *  fallback, the federal or national directory it is. */
function destination(step: VoteStep): string {
  if (step.task === "sample_ballot" && !step.isStateSpecific) {
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

function Step({ step, n }: { step: VoteStep; n: number }) {
  const label = STEP_LABEL[step.task];
  const href = safeHref(step.url ?? "");
  const deadline = deadlineText(step);
  const closed = step.deadline?.status === "closed";
  const fallbackHref = step.fallback ? safeHref(step.fallback.url) : null;
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
          aria-label={`${label}: ${where}${step.unconfirmed ? ", unconfirmed" : ""}${deadline ? `. ${deadline}` : ""} (opens in new tab)`}
          className="flex min-h-[52px] items-start gap-3 px-4 py-3 hover:bg-white/[0.04] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
        >
          {body}
        </a>
      ) : (
        // A state with no voter registration: said, with no link to follow.
        <div className="flex min-h-[52px] items-start gap-3 px-4 py-3">{body}</div>
      )}
      {step.unconfirmed && fallbackHref && step.fallback && (
        <p className="border-t border-white/[0.09] px-4 py-2 pl-[52px] text-xs text-ink-lo wrap-anywhere">
          This site blocks automated checks, so we can&apos;t confirm it&apos;s up. If it
          doesn&apos;t open, use{" "}
          <a
            href={fallbackHref}
            target="_blank"
            rel="noopener noreferrer"
            className={INLINE_LINK}
            aria-label={`${label} through ${step.fallback.sourceName} (opens in new tab)`}
          >
            {step.fallback.host} ↗
          </a>
        </p>
      )}
    </li>
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
