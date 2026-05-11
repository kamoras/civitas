"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ScoreKey, ScoreTerm } from "@/lib/plainLanguage";
import { PLAIN_TERMS, TECHNICAL_TERMS } from "@/lib/plainLanguage";

const STORAGE_KEY = "civitas_plain_language";

interface PlainLanguageContextValue {
  plain: boolean;
  toggle: () => void;
  terms: (key: ScoreKey) => ScoreTerm;
}

const PlainLanguageContext = createContext<PlainLanguageContextValue>({
  plain: false,
  toggle: () => {},
  terms: (key) => TECHNICAL_TERMS[key],
});

export function PlainLanguageProvider({ children }: { children: React.ReactNode }) {
  const [plain, setPlain] = useState(false);

  useEffect(() => {
    try {
      setPlain(localStorage.getItem(STORAGE_KEY) === "1");
    } catch {}
  }, []);

  const toggle = useCallback(() => {
    setPlain((v) => {
      const next = !v;
      try { localStorage.setItem(STORAGE_KEY, next ? "1" : "0"); } catch {}
      return next;
    });
  }, []);

  const terms = useCallback(
    (key: ScoreKey): ScoreTerm => (plain ? PLAIN_TERMS[key] : TECHNICAL_TERMS[key]),
    [plain],
  );

  return (
    <PlainLanguageContext.Provider value={{ plain, toggle, terms }}>
      {children}
    </PlainLanguageContext.Provider>
  );
}

export function usePlainLanguage() {
  return useContext(PlainLanguageContext);
}
