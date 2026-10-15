import RecordNotFound from "@/components/notFound/RecordNotFound";

/** Any address no route answers. Record pages that miss (a member, a bill,
 * a vote, an issue, a document) have their own not-found.tsx, with the
 * next step for that kind of record. */
export default function NotFound() {
  return <RecordNotFound />;
}
