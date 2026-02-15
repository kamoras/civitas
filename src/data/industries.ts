import { IndustryCode } from "@/types/senator";

export interface IndustryInfo {
  code: IndustryCode;
  name: string;
  color: string;
}

export const INDUSTRIES: Record<IndustryCode, IndustryInfo> = {
  PHARMA: { code: "PHARMA", name: "Pharmaceuticals", color: "#ff4444" },
  INSURANCE: { code: "INSURANCE", name: "Insurance", color: "#ff8844" },
  OIL_GAS: { code: "OIL_GAS", name: "Oil & Gas", color: "#884400" },
  DEFENSE: { code: "DEFENSE", name: "Defense & Aerospace", color: "#666666" },
  FINANCE: { code: "FINANCE", name: "Wall Street", color: "#44aa44" },
  REAL_ESTATE: { code: "REAL_ESTATE", name: "Real Estate", color: "#8888ff" },
  TECH: { code: "TECH", name: "Big Tech", color: "#00aaff" },
  TELECOM: { code: "TELECOM", name: "Telecom", color: "#aa44ff" },
  AGRIBUSINESS: { code: "AGRIBUSINESS", name: "Agribusiness", color: "#aaaa00" },
  ENERGY: { code: "ENERGY", name: "Energy Utilities", color: "#ffaa00" },
  CONSTRUCTION: { code: "CONSTRUCTION", name: "Construction", color: "#aa8844" },
  TRANSPORT: { code: "TRANSPORT", name: "Transportation", color: "#4488aa" },
  LAWYERS: { code: "LAWYERS", name: "Lawyers & Lobbyists", color: "#aa4488" },
  LOBBYISTS: { code: "LOBBYISTS", name: "Lobbyists", color: "#ff44aa" },
  GAMBLING: { code: "GAMBLING", name: "Casinos & Gambling", color: "#ffdd00" },
  GUNS: { code: "GUNS", name: "Firearms", color: "#ff0000" },
  TOBACCO: { code: "TOBACCO", name: "Tobacco", color: "#886644" },
  CRYPTO: { code: "CRYPTO", name: "Crypto", color: "#ff8800" },
  PRIVATE_PRISON: { code: "PRIVATE_PRISON", name: "Private Prisons", color: "#444444" },
  OTHER: { code: "OTHER", name: "Other", color: "#888888" },
};
