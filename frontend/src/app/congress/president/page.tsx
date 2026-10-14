import { redirect } from "next/navigation";
import { presidentSupportHref } from "@/lib/presidentialSupport";

/** The House opens the section: it has the larger sample. */
export default function PresidentSupportIndex() {
  redirect(presidentSupportHref("house"));
}
