import type { ReactNode } from "react";
import RecordBox from "@/components/shared/RecordBox";
import type { MemberBox as Box } from "@/types/politicians";
import { onThisPage } from "@/lib/onThisPage";
import { TYPE } from "@/lib/typeStyles";

/** A labelled block inside a box's More, set off by a hairline above it. */
export function Block({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-2 border-t border-white/[0.08] pt-4 first:border-t-0 first:pt-0">
      <h4 className={TYPE.kicker}>{label}</h4>
      {children}
    </div>
  );
}

/**
 * One box of a member's record (BoxGrid's RecordBox): the backend's kicker,
 * figure and caption as the title ("2 of 89 / passed the Senate"), its one
 * line, a chart, and the full record behind "More". Every word of the box is
 * the API's (services/member_boxes.py); from a backend older than the boxes
 * the box keeps a fixed label for a title and the breakdown's finding for a
 * line, and counts nothing itself.
 */
export default function MemberBox({
  id,
  label,
  box,
  fallback,
  children,
  more,
  about,
}: {
  /** The box's anchor (the section chips and shared images link to it). */
  id: string;
  /** Its name in the section chips and a phone's "on this page" bar. */
  label: string;
  box: Box | null | undefined;
  /** Kicker, title and line from an older backend. */
  fallback: { kicker: string; title: string; dek?: string | null };
  children?: ReactNode;
  more?: { label: string; children: ReactNode } | null;
  /** What the box measures and where it comes from, in plain words: the
   *  last paragraph behind More. */
  about?: string;
}) {
  const figure = box?.figure;
  const title = figure ? (
    <>
      <span className={`${TYPE.figure} block`}>{figure.text ?? figure.value}</span>
      <span className="block">{figure.caption}</span>
    </>
  ) : (
    (box?.headline ?? fallback.title)
  );
  return (
    <RecordBox
      kicker={box?.kicker ?? fallback.kicker}
      title={title}
      dek={box ? box.dek : fallback.dek}
      id={id}
      attrs={onThisPage(label)}
      share={id}
      more={
        more && {
          label: more.label,
          children: (
            <>
              {more.children}
              {about && <p className="mt-4 text-small text-ink-lo">{about}</p>}
            </>
          ),
        }
      }
    >
      {children}
    </RecordBox>
  );
}
