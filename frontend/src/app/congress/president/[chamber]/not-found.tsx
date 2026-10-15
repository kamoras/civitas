import RecordNotFound from "@/components/notFound/RecordNotFound";

/** Anything but /senate or /house.
 * It had none, so it fell to the site's generic 404. */
export default function WithThePresidentNotFound() {
  return (
    <RecordNotFound
      label="No chamber at this address"
      message="Votes with the president are listed for the Senate and the House."
      steps={[
        {
          href: "/congress/president/senate",
          label: "How often each senator votes with the president",
        },
        {
          href: "/congress/president/house",
          label: "How often each House member votes with the president",
        },
      ]}
    />
  );
}
