import RecordNotFound from "@/components/notFound/RecordNotFound";
import CoverageNote from "@/components/congress/CoverageNote";

/** A roll call address with no vote recorded behind it: the reports list
 * every roll call by the day it was taken. */
export default function RollCallNotFound() {
  return (
    <RecordNotFound
      label="No vote at this address"
      message="Civitas holds no roll call at this address. Each day's report lists that day's votes."
      steps={[
        { href: "/congress", label: "What Congress did on its latest day, every vote linked" },
        { href: "/congress/bills", label: "Bills moving in Congress, and the votes on each" },
      ]}
    >
      <CoverageNote of="rollCalls" />
    </RecordNotFound>
  );
}
