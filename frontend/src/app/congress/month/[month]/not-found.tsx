import RecordNotFound from "@/components/notFound/RecordNotFound";
import CoverageNote from "@/components/congress/CoverageNote";

/** A month with no day on record, or not a month.
 * It had none, so it fell to the site's generic 404. */
export default function CongressMonthNotFound() {
  return (
    <RecordNotFound
      label="No Congress month at this address"
      message="No day of this month is in Civitas's record of Congress."
      steps={[
        { href: "/congress", label: "What Congress did on its latest day, and its month" },
        { href: "/congress/bills", label: "Bills moving in Congress" },
      ]}
    >
      <CoverageNote of="days" />
    </RecordNotFound>
  );
}
