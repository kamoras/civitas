import Link from "next/link";
import { agencyHref } from "@/lib/routes";

/** An issuing agency's name, linking its page when the document says which
 * agency it is (the slug, read from the Federal Register); plain text for a
 * document whose agency hasn't been read yet. */
export default function AgencyLink({
  name,
  slug,
  className = "underline decoration-white/30 underline-offset-2 hover:text-phos",
}: {
  name: string | null | undefined;
  slug: string | null | undefined;
  className?: string;
}) {
  if (!name) return null;
  return slug ? (
    <Link href={agencyHref(slug)} className={className}>
      {name}
    </Link>
  ) : (
    <>{name}</>
  );
}
