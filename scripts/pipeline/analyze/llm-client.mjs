import { GoogleGenerativeAI } from "@google/generative-ai";
import { GEMINI_API_KEY, GEMINI_MODEL, MAX_RETRIES, log } from "../config.mjs";
import { analysisGet, analysisSet } from "../cache/store.mjs";

let genAI = null;
let totalCalls = 0;
let totalInputTokens = 0;
let totalOutputTokens = 0;
let lastCallTime = 0;
const MIN_CALL_INTERVAL_MS = 4500; // ~13 req/min, safely under 15/min free tier

function getClient() {
  if (!genAI) {
    if (!GEMINI_API_KEY) {
      throw new Error("GEMINI_API_KEY not set. Add it to your .env file.");
    }
    genAI = new GoogleGenerativeAI(GEMINI_API_KEY);
  }
  return genAI;
}

/**
 * Call Gemini with structured JSON output.
 * Results are cached by prompt version + input hash.
 *
 * @param {Object} options
 * @param {string} options.promptVersion - Version string for caching
 * @param {string} options.systemPrompt - System instruction
 * @param {string} options.userPrompt - User message
 * @param {*} options.cacheKey - Data used to generate the cache key
 * @param {string} [options.model] - Override model
 * @param {number} [options.maxTokens] - Max output tokens (default 2048)
 * @returns {Object|null} Parsed JSON response, or null on failure
 */
export async function callClaude({
  promptVersion,
  systemPrompt,
  userPrompt,
  cacheKey,
  model,
  maxTokens = 2048,
}) {
  // Check analysis cache first
  const cached = analysisGet(promptVersion, cacheKey);
  if (cached) {
    log.debug(`LLM cache hit: ${promptVersion}`);
    return cached;
  }

  const ai = getClient();
  const useModel = model || GEMINI_MODEL;

  const maxAttempts = MAX_RETRIES + 3; // Extra retries for rate limits
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    // Rate limiting — wait between calls
    const elapsed = Date.now() - lastCallTime;
    if (elapsed < MIN_CALL_INTERVAL_MS) {
      await new Promise((r) => setTimeout(r, MIN_CALL_INTERVAL_MS - elapsed));
    }
    lastCallTime = Date.now();

    try {
      log.debug(`Gemini call: ${promptVersion} (attempt ${attempt}, model: ${useModel})`);

      const generativeModel = ai.getGenerativeModel({
        model: useModel,
        systemInstruction: systemPrompt,
        generationConfig: {
          maxOutputTokens: maxTokens,
          responseMimeType: "application/json",
        },
      });

      const result = await generativeModel.generateContent(userPrompt);
      const response = result.response;
      const text = response.text();

      // Track token usage
      const usage = response.usageMetadata;
      if (usage) {
        totalInputTokens += usage.promptTokenCount || 0;
        totalOutputTokens += usage.candidatesTokenCount || 0;
      }
      totalCalls++;

      // Parse JSON from response
      const parsed = extractJSON(text);
      if (parsed === null) {
        log.warn(`Gemini returned non-JSON for ${promptVersion}, attempt ${attempt}`);
        if (attempt < MAX_RETRIES) {
          await new Promise((r) => setTimeout(r, 2000 * attempt));
          continue;
        }
        log.error(`Gemini JSON parse failed for ${promptVersion}. Raw: ${text.slice(0, 500)}`);
        return null;
      }

      // Cache the result
      analysisSet(promptVersion, cacheKey, parsed);
      return parsed;
    } catch (e) {
      const status = e.status || e.httpStatusCode;
      if (status === 429) {
        // Parse retry delay from error if available
        const retryMatch = e.message?.match(/retry in ([\d.]+)s/i);
        const wait = retryMatch
          ? Math.ceil(parseFloat(retryMatch[1]) * 1000) + 1000
          : 30000 * attempt; // Default 30s+ backoff for free tier
        log.warn(`Gemini rate limited, waiting ${(wait / 1000).toFixed(0)}s...`);
        await new Promise((r) => setTimeout(r, wait));
        continue;
      }

      if (attempt === maxAttempts) {
        log.error(`Gemini call failed for ${promptVersion}:`, e.message);
        return null;
      }
      await new Promise((r) => setTimeout(r, 2000 * attempt));
    }
  }
  return null;
}

/**
 * Extract JSON from LLM response text.
 */
function extractJSON(text) {
  // Try direct parse first
  try {
    return JSON.parse(text.trim());
  } catch {}

  // Try extracting from markdown code block
  const codeBlockMatch = text.match(/```(?:json)?\s*([\s\S]*?)```/);
  if (codeBlockMatch) {
    try {
      return JSON.parse(codeBlockMatch[1].trim());
    } catch {}
  }

  // Try finding JSON array or object
  const jsonMatch = text.match(/(\[[\s\S]*\]|\{[\s\S]*\})/);
  if (jsonMatch) {
    try {
      return JSON.parse(jsonMatch[1]);
    } catch {}
  }

  return null;
}

/**
 * Get LLM usage statistics.
 */
export function getLLMStats() {
  return {
    totalCalls,
    totalInputTokens,
    totalOutputTokens,
    estimatedCost: "free (Gemini)",
  };
}
