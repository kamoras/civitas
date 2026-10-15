import RecordNotFound from "@/components/notFound/RecordNotFound";

/** A date Congress isn't on record as meeting, or not a date: the page
 * names what it looked for and the way to the days that are.
 * It had none, so it fell to the site's generic 404. */
export default function CongressDayNotFound() {
  return (
    <RecordNotFound
      label="No Congress day at this address"
      message="Neither chamber is on record as meeting on this date. Each day either chamber met has a report."
      steps={[
        { href: "/congress", label: "What Congress did on its latest day" },
        { href: "/congress/bills", label: "Bills moving in Congress" },
      ]}
    />
  );
}
