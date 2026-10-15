import RecordNotFound from "@/components/notFound/RecordNotFound";
import StateFromAddress from "@/components/notFound/StateFromAddress";

/** A state ballot address that names no state with a ballot on record:
 * the state it seems to mean, then the elections map, which lists every
 * state's ballot. */
export default function StateBallotNotFound() {
  return (
    <RecordNotFound
      label="No ballot at this address"
      message="No state's ballot has this address. State pages use the two-letter code, such as /elections/states/OH."
      steps={[{ href: "/elections", label: "Every state's ballot, from the elections map" }]}
    >
      <StateFromAddress />
    </RecordNotFound>
  );
}
