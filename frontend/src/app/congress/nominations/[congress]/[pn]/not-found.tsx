import RecordNotFound from "@/components/notFound/RecordNotFound";
import { NOMINATIONS_HREF } from "@/lib/nominations";

/** A nomination address with no nomination behind it (in the Congress it names). */
export default function NominationNotFound() {
  return (
    <RecordNotFound
      label="No nomination at this address"
      message="Civitas has no nomination with this citation in this Congress. Citations start again at PN1 in each Congress, and a new nomination appears here the day after Congress.gov lists it."
      steps={[
        { href: NOMINATIONS_HREF, label: "Every nomination this Congress, by status" },
        { href: "/congress", label: "What Congress did on its latest day" },
      ]}
    />
  );
}
