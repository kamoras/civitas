import RecordNotFound from "@/components/notFound/RecordNotFound";
import { ACTION_CENTER_ARCHIVE_HREF, ACTION_CENTER_HREF } from "@/lib/routes";

/** An Action Center issue address with no issue behind it. */
export default function IssueNotFound() {
  return (
    <RecordNotFound
      label="No issue at this address"
      message="No Action Center issue has this address."
      steps={[
        { href: ACTION_CENTER_HREF, label: "Today's issues and what you can do about each" },
        { href: ACTION_CENTER_ARCHIVE_HREF, label: "The archive: every day's top issue" },
      ]}
    />
  );
}
