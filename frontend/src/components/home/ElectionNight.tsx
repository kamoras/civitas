import Link from "@/components/Link";
import type { ResultsSummary } from "@/types/election";
import { formatEasternTime } from "@/lib/results";
import { fetchResultsSummary } from "@/lib/resultsSummaryServer";
import { INLINE_LINK } from "@/lib/controlStyles";
import { TYPE } from "@/lib/typeStyles";

/**
 * One line at the top of the homepage while election results are up
 * (backend election_phase: from election night until the count settles,
 * which can run to January 3): how many states are counting, when the
 * count last moved, and the way to the count itself. Outside that window,
 * or before any state has counted, it renders nothing.
 *
 * One line, not the count: until 2026-11 this listed five states' leaders
 * in full, a screen of the homepage for the whole window. Each state's
 * count, worded by the backend's templates ("leads", "not final", never
 * "wins"), is on /elections and the state's page.
 */
export default async function ElectionNight() {
  const data = await fetchResultsSummary();
  if (!data || data.phase.phase === "campaign" || data.states.length === 0) return null;
  return <ElectionNightList data={data} />;
}

export function ElectionNightList({ data }: { data: ResultsSummary }) {
  const counting = data.states.length;
  const changed = data.phase.lastResultChange ? formatEasternTime(data.phase.lastResultChange) : "";
  return (
    <section
      aria-labelledby="election-night"
      className="mx-auto mt-6 flex max-w-7xl flex-wrap items-baseline gap-x-3 gap-y-1 px-4 sm:px-6"
    >
      <h2 id="election-night" className={TYPE.kicker}>
        Election results
      </h2>
      <p className={TYPE.small}>
        {counting} {counting === 1 ? "state" : "states"} counting
        {changed && <> · last change {changed}</>}
      </p>
      <p className={TYPE.small}>
        <Link href="/elections" className={INLINE_LINK}>
          See the count
        </Link>
        {" · "}
        <Link href="/about/elections" className={INLINE_LINK}>
          How results are read
        </Link>
      </p>
    </section>
  );
}
