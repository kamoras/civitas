"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { AppConfig, fetchConfig, IndustryInfo } from "@/lib/api";
import { DEFAULT_WEIGHTS } from "@/lib/corruption";

const ConfigContext = createContext<AppConfig | null>(null);

export const ConfigProvider = ConfigContext.Provider;

export function useConfigLoader(): AppConfig | null {
  const [config, setConfig] = useState<AppConfig | null>(null);

  useEffect(() => {
    fetchConfig().then(setConfig);
  }, []);

  return config;
}

export function useConfig(): AppConfig | null {
  return useContext(ConfigContext);
}

export function useIndustries(): Record<string, IndustryInfo> {
  const config = useConfig();
  return config?.industries ?? {};
}

export function useCategoryLabel(key: string): string {
  const config = useConfig();
  return config?.platformCategories[key] ?? key.toUpperCase();
}

export function usePolicyLabel(area: string): string {
  return area
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function useScoreWeights(): Record<string, number> {
  const config = useConfig();
  return config?.scoreWeights ?? DEFAULT_WEIGHTS;
}
