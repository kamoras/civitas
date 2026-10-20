"use client";

import Link from "next/link";
import Photo from "@/components/Photo";
import { useAsyncData } from "@/hooks/useAsyncData";
import { fetchJusticesAppointedBy } from "@/lib/api";
import { COURT_HREF } from "@/lib/court";

function year(iso: string | null): string {
  return iso ? iso.slice(0, 4) : "";
}

/**
 * The Supreme Court justices a president appointed, each linked to their
 * page: the other half of the justice page's "appointed by" link. Covers
 * the justices the site holds a record for (those who have sat since the
 * record began); renders nothing for a president who appointed none of
 * them, or while it loads.
 */
export default function JusticesAppointed({ presidentId }: { presidentId: string }) {
  const { data } = useAsyncData(`justices-appointed/${presidentId}`, () =>
    fetchJusticesAppointedBy(presidentId)
  );
  if (!data || data.length === 0) return null;
  return (
    <section
      id="justices-appointed"
      aria-labelledby="justices-appointed-heading"
      className="mt-6 border border-white/25 bg-surface px-4 py-4 font-sans sm:px-6"
    >
      <h2 id="justices-appointed-heading" className="text-[19px] font-semibold text-ink-hi">
        Justices appointed
      </h2>
      <ul className="mt-3 grid grid-cols-[minmax(0,1fr)] gap-3 sm:grid-cols-2">
        {data.map((j) => (
          <li key={j.id}>
            <Link
              href={`/politicians/${encodeURIComponent(j.id)}`}
              className="flex min-h-11 items-center gap-3 hover:text-phos"
            >
              {j.photo && (
                <Photo
                  src={j.photo}
                  alt=""
                  sizes="40px"
                  lazy
                  className="h-12 w-10 shrink-0 border border-white/25 object-cover"
                />
              )}
              <span className="min-w-0">
                <span className="block font-semibold text-ink-hi underline-offset-2 hover:underline">
                  {j.name}
                </span>
                <span className="block text-sm text-ink-lo">
                  {j.isActive
                    ? `On the Court since ${year(j.dateStart)}`
                    : `Served ${year(j.dateStart)} to ${year(j.dateEnd)}`}
                </span>
              </span>
            </Link>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-sm">
        <Link href={COURT_HREF} className="text-phos underline underline-offset-2">
          The whole Court
        </Link>
      </p>
    </section>
  );
}
