import RecordNotFound from "@/components/notFound/RecordNotFound";

/** A month with no day on record, or not a month.
 * It had none, so it fell to the site's generic 404. */
export default function CongressMonthNotFound() {
  return (
    <RecordNotFound
      label="No Congress month at this address"
      message="No day of this month is on record for either chamber."
      steps={[
        { href: "/congress", label: "What Congress did on its latest day, and its month" },
        { href: "/congress/bills", label: "Bills moving in Congress" },
      ]}
    />
  );
}
