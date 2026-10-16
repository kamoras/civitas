import RecordNotFound from "@/components/notFound/RecordNotFound";
import CoverageNote from "@/components/congress/CoverageNote";

/** A week with no day on record, or not a date.
 * It had none, so it fell to the site's generic 404. */
export default function CongressWeekNotFound() {
  return (
    <RecordNotFound
      label="No Congress week at this address"
      message="No day of this week is in Civitas's record of Congress."
      steps={[
        { href: "/congress", label: "What Congress did on its latest day, and its week" },
        { href: "/congress/bills", label: "Bills moving in Congress" },
      ]}
    >
      <CoverageNote of="days" />
    </RecordNotFound>
  );
}
