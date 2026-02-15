import { callClaude } from "./llm-client.mjs";
import { nicknamePrompt } from "./prompts.mjs";
import { log } from "../config.mjs";

/**
 * Generate a punk nickname for a senator based on their data profile.
 * @param {Object} senator - Fully assembled senator record
 * @returns {string} Punk nickname
 */
export async function generateNickname(senator) {
  const { promptVersion, systemPrompt, userPrompt } = nicknamePrompt(senator);

  const result = await callClaude({
    promptVersion,
    systemPrompt,
    userPrompt,
    cacheKey: {
      senatorId: senator.id,
      topIndustry: senator.funding?.industryBreakdown?.[0]?.industry,
      score: senator.corruptionScore?.corporateFunding,
    },
    maxTokens: 256,
  });

  if (result?.punkNickname) {
    return result.punkNickname;
  }

  // Fallback: generate a basic nickname from data
  log.warn(`Nickname generation failed for ${senator.name}, using fallback`);
  return generateFallbackNickname(senator);
}

function generateFallbackNickname(senator) {
  const topIndustry = senator.funding?.industryBreakdown?.[0]?.name || "Corporate";
  const partyLabel = senator.party === "R" ? "Red" : senator.party === "D" ? "Blue" : "Independent";
  return `${topIndustry} ${partyLabel}`;
}
