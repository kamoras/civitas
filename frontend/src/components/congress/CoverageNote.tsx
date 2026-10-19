import { fetchCoverage } from "@/lib/congressServer";

const LINK = "underline decoration-white/30 underline-offset-4 hover:text-phos";

/**
 * Where Civitas's record of Congress starts, for a 404 under /congress: a
 * date, week, month or roll call before it isn't "nothing happened", it is
 * outside what is held (a day before the record said neither chamber met,
 * when both had). The sentence is the backend's (congress_service.coverage), with
 * a link to the record that has the rest. Nothing when the backend can't say.
 */
export default async function CoverageNote({ of }: { of: "days" | "rollCalls" }) {
  const coverage = await fetchCoverage();
  if (!coverage) return null;
  return (
    <p className="mt-3 font-sans text-sm leading-relaxed text-ink-lo">
      {of === "days" ? coverage.sentence : coverage.rollCallSentence}{" "}
      {of === "days" ? (
        <>
          Earlier days:{" "}
          <a href={coverage.congressionalRecordUrl} className={LINK}>
            the Congressional Record at GovInfo
          </a>
          .
        </>
      ) : (
        <>
          Earlier votes:{" "}
          <a href={coverage.rollCallSources.senate} className={LINK}>
            Senate.gov
          </a>
          {", "}
          <a href={coverage.rollCallSources.house} className={LINK}>
            the House Clerk
          </a>
          .
        </>
      )}
    </p>
  );
}
