"use client";

import Link from "next/link";
import Photo from "@/components/Photo";
import { useAsyncData } from "@/hooks/useAsyncData";
import { fetchJusticesAppointedBy } from "@/lib/api";
import { COURT_HREF } from "@/lib/court";
import type { AppointedJustice } from "@/types/justice";

function year(iso: string | null): string {
  return iso ? iso.slice(0, 4) : "";
}

function Entry({ j }: { j: AppointedJustice }) {
  const text = (
    <span className="min-w-0">
      <span
        className={`block font-semibold text-ink-hi ${j.id ? "underline-offset-2 group-hover:underline" : ""}`}
      >
        {j.name}
      </span>
      <span className="block text-sm text-ink-lo">
        {j.seat} ·{" "}
        {j.isActive
          ? `on the Court since ${year(j.dateStart)}`
          : year(j.dateStart) === year(j.dateEnd)
            ? `in ${year(j.dateStart)}`
            : `${year(j.dateStart)} to ${year(j.dateEnd)}`}
      </span>
    </span>
  );
  if (!j.id) return <div className="flex min-h-11 items-center">{text}</div>;
  return (
    <Link
      href={`/politicians/${encodeURIComponent(j.id)}`}
      className="group flex min-h-11 items-center gap-3 hover:text-phos"
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
      {text}
    </Link>
  );
}

/**
 * Every Supreme Court appointment a president made, from the Federal
 * Judicial Center's directory (stored by the justice pipeline): the other
 * half of the justice page's "appointed by" link. A justice with a page on
 * the site links to it; one without is named with the years served.
 * Renders nothing for a president who made no appointment, or while it
 * loads.
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
      <p className="mt-1 text-sm text-ink-lo">
        {data.length} Supreme Court appointment{data.length === 1 ? "" : "s"}, from the Federal
        Judicial Center.
      </p>
      <ul className="mt-3 grid grid-cols-[minmax(0,1fr)] gap-3 sm:grid-cols-2">
        {data.map((j, i) => (
          <li key={`${j.id ?? j.name}-${i}`}>
            <Entry j={j} />
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
