"use client";

import { useState, useEffect } from "react";

export function useUserState(): [string | null, (s: string | null) => void] {
  const [state, setState] = useState<string | null>(null);
  useEffect(() => {
    if (typeof window !== "undefined") {
      const saved = localStorage.getItem("civitas_user_state");
      if (saved) setState(saved);
    }
  }, []);
  const setAndPersist = (s: string | null) => {
    setState(s);
    if (typeof window !== "undefined") {
      if (s) localStorage.setItem("civitas_user_state", s);
      else localStorage.removeItem("civitas_user_state");
    }
  };
  return [state, setAndPersist];
}
