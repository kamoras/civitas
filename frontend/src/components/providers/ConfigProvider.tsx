"use client";

import { ReactNode } from "react";
import { ConfigProvider as CtxProvider, useConfigLoader } from "@/hooks/useConfig";

export default function ConfigProvider({ children }: { children: ReactNode }) {
  const config = useConfigLoader();
  return <CtxProvider value={config}>{children}</CtxProvider>;
}
