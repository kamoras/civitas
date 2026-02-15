import { log } from "../config.mjs";
import { validateSenator } from "./validator.mjs";

/**
 * Assemble a complete Senator record from all pipeline data.
 *
 * @param {Object} baseSenator - From normalize-members.mjs
 * @param {Object} funding - From normalize-finance.mjs
 * @param {Object} votingRecord - From normalize-votes.mjs (with cross-reference data)
 * @param {Array} lobbyingMatches - From cross-reference.mjs
 * @param {Object} corruptionScore - From score-calculator.mjs
 * @param {string} punkNickname - From nickname-generator.mjs
 * @returns {Object} Complete, validated Senator record
 */
export function buildSenator(
  baseSenator,
  funding,
  votingRecord,
  lobbyingMatches,
  corruptionScore,
  punkNickname
) {
  const senator = {
    ...baseSenator,
    punkNickname: punkNickname || baseSenator.punkNickname || "TBD",
    corruptionScore: corruptionScore || baseSenator.corruptionScore,
    funding: funding || baseSenator.funding,
    votingRecord: votingRecord || baseSenator.votingRecord,
    lobbyingMatches: lobbyingMatches || baseSenator.lobbyingMatches,
  };

  return validateSenator(senator);
}
