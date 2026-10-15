import RecordNotFound from "@/components/notFound/RecordNotFound";
import StateFromAddress from "@/components/notFound/StateFromAddress";
import { REDISTRICTING_HREF } from "@/lib/redistricting";

/** A redistricting address for a state that didn't redraw its House map
 * for this election (or no state at all): that state's ballot, the states
 * that did redraw, and the elections map. */
export default function StateRedistrictingNotFound() {
  return (
    <RecordNotFound
      label="No new map at this address"
      message="This state didn't redraw its U.S. House map for this election, or the address names no state."
      steps={[
        { href: REDISTRICTING_HREF, label: "The states that redrew their House maps" },
        { href: "/elections", label: "Every state's ballot, from the elections map" },
      ]}
    >
      <StateFromAddress />
    </RecordNotFound>
  );
}
