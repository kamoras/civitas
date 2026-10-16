import RecordNotFound from "@/components/notFound/RecordNotFound";
import CoverageNote from "@/components/congress/CoverageNote";

/** A date Congress isn't on record as meeting, or not a date: the page
 * names what it looked for and the way to the days that are.
 * It had none, so it fell to the site's generic 404. */
export default function CongressDayNotFound() {
  return (
    <RecordNotFound
      label="No Congress day at this address"
      message="This date is outside Civitas's record of Congress (before its first day, or still to come), or isn't a date. Each day since the record starts has a report, whether or not either chamber met."
      steps={[
        { href: "/congress", label: "What Congress did on its latest day" },
        { href: "/congress/bills", label: "Bills moving in Congress" },
      ]}
    >
      <CoverageNote of="days" />
    </RecordNotFound>
  );
}
