import RecordNotFound from "@/components/notFound/RecordNotFound";

/** A week with no day on record, or not a date.
 * It had none, so it fell to the site's generic 404. */
export default function CongressWeekNotFound() {
  return (
    <RecordNotFound
      label="No Congress week at this address"
      message="No day of this week is on record for either chamber."
      steps={[
        { href: "/congress", label: "What Congress did on its latest day, and its week" },
        { href: "/congress/bills", label: "Bills moving in Congress" },
      ]}
    />
  );
}
