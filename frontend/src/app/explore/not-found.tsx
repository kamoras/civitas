import RecordNotFound from "@/components/notFound/RecordNotFound";

/** A document address with no document behind it (`explore/[id]/page`
 * calls notFound(), so this segment's boundary renders). */
export default function ExploreNotFound() {
  return (
    <RecordNotFound
      label="No document at this address"
      message="No speech, rule, presidential action or opinion has this address."
      steps={[{ href: "/explore", label: "Search every document by words, topic or citation" }]}
    />
  );
}
