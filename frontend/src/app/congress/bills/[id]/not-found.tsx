import RecordNotFound from "@/components/notFound/RecordNotFound";

/** A bill address with no bill behind it (in the Congress it names). */
export default function BillNotFound() {
  return (
    <RecordNotFound
      label="No bill at this address"
      message="Congress.gov has no bill with this number in this Congress. A bill number names a different bill in each Congress."
      steps={[
        { href: "/congress/bills", label: "Bills moving in Congress, by stage and title" },
        { href: "/congress", label: "What Congress did on its latest day" },
      ]}
    />
  );
}
