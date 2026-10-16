import RecordNotFound from "@/components/notFound/RecordNotFound";
import { ELECTIONS_ARCHIVE_HREF } from "@/lib/routes";

/** An election Civitas kept no archive of, or a race id no election
 * holds: the archived elections, and the current ballots. */
export default function ElectionsSegmentNotFound() {
  return (
    <RecordNotFound
      label="No election or race at this address"
      message="Civitas kept no record of an election or race by this name. Archives start with the first general election Civitas covered from its election day."
      steps={[
        { href: ELECTIONS_ARCHIVE_HREF, label: "Every archived general election" },
        { href: "/elections", label: "Every state's ballot for the coming election" },
      ]}
    />
  );
}
