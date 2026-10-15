import RecordNotFound from "@/components/notFound/RecordNotFound";
import MemberSuggestions from "@/components/notFound/MemberSuggestions";

/** A member, president or justice address that matched no one: the
 * closest names first, then the directory. */
export default function PoliticianNotFound() {
  return (
    <RecordNotFound
      label="No politician at this address"
      message="No member of Congress, president or justice has this address."
      steps={[{ href: "/politicians", label: "Find them in the directory, by name or state" }]}
    >
      <MemberSuggestions />
    </RecordNotFound>
  );
}
