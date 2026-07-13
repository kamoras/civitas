"use client";

import { useState, useEffect } from "react";
import { Senator } from "@/types/senator";
import { fetchSenatorsByState } from "@/lib/api";

export function useSenatorsByState(state: string) {
  const [senators, setSenators] = useState<Senator[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!state) {
      setSenators([]);
      return;
    }

    setLoading(true);
    setError(null);

    fetchSenatorsByState(state)
      .then(setSenators)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [state]);

  return { senators, loading, error };
}
