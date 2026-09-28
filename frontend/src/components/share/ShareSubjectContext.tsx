"use client";

import { createContext, useContext, type ReactNode } from "react";
import type { ShareSubject } from "@/lib/shareImage";

const ShareSubjectContext = createContext<ShareSubject | null>(null);

/**
 * Names what a page is about, for every `ShareSectionButton` inside it: the
 * shared image's title strip and link come from here, so a section doesn't
 * need to know whose page it is on. A section rendered outside a provider
 * (a compare page, a test) simply shows no share button.
 */
export function ShareSubjectProvider({
  subject,
  children,
}: {
  subject: ShareSubject;
  children: ReactNode;
}) {
  return <ShareSubjectContext.Provider value={subject}>{children}</ShareSubjectContext.Provider>;
}

export function useShareSubject(): ShareSubject | null {
  return useContext(ShareSubjectContext);
}
