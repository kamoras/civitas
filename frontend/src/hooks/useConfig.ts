"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { AppConfig, fetchConfig, IndustryInfo } from "@/lib/api";

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

export function useIndustryInfo(code: string): IndustryInfo {
  const config = useConfig();
  return config?.industries[code] ?? { name: code.replace(/_/g, " "), color: "#444444" };
}

export function useIndustries(): Record<string, IndustryInfo> {
  const config = useConfig();
  return config?.industries ?? {};
}

export function useCategoryLabel(key: string): string {
  const config = useConfig();
  return config?.platformCategories[key] ?? key.toUpperCase();
}

export function useScoreWeights(): Record<string, number> {
  const config = useConfig();
  return config?.scoreWeights ?? {
    constituentFunding: 0.25,
    promiseFulfillment: 0.20,
    independenceIndex: 0.25,
    donorDiversity: 0.10,
    accountability: 0.20,
  };
}
