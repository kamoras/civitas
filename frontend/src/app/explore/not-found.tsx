import { SEARCH_DOCUMENTS_HREF } from "@/lib/routes";
import RecordNotFound from "@/components/notFound/RecordNotFound";
import ExploreOfficialSource from "@/components/notFound/ExploreOfficialSource";

/** A document address with no document behind it (`explore/[id]/page`
 * calls notFound(), so this segment's boundary renders). A permanent key
 * names its document's official identifier, so even a key nothing here
 * answers leads to the official copy (ExploreOfficialSource). */
export default function ExploreNotFound() {
  return (
    <RecordNotFound
      label="No document at this address"
      message="No speech, rule, presidential action or opinion has this address."
      steps={[
        { href: SEARCH_DOCUMENTS_HREF, label: "Search every document by words, topic or citation" },
      ]}
    >
      <ExploreOfficialSource />
    </RecordNotFound>
  );
}
