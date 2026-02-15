import "dotenv/config";

import { DATA_GOV_API_KEY, GEMINI_API_KEY, KEY_BILLS, CURRENT_CONGRESS, log } from "./config.mjs";
import { getCacheStats } from "./cache/store.mjs";

// Fetch modules
import {
  fetchSenators,
  fetchMemberDetail,
  fetchBill,
  fetchBillActions,
  fetchBillSummaries,
  fetchRollCallVote,
} from "./fetch/congress.mjs";
import {
  findCandidate,
  fetchCandidateFinancials,
  fetchCandidateCommittees,
  fetchCommitteeReceipts,
  fetchPACReceipts,
  fetchAggregatedContributors,
} from "./fetch/fec.mjs";
import { fetchBillText } from "./fetch/govinfo.mjs";

// Transform modules
import { normalizeMembers } from "./transform/normalize-members.mjs";
import { normalizeFinance } from "./transform/normalize-finance.mjs";
import {
  normalizeVotes,
  findSenateRollCall,
  extractSenatorVote,
} from "./transform/normalize-votes.mjs";

// Analyze modules
import { classifyAllBills } from "./analyze/bill-analyzer.mjs";
import { analyzeSenatorBatch } from "./analyze/cross-reference.mjs";
import { calculateScores } from "./analyze/score-calculator.mjs";
import { getLLMStats } from "./analyze/llm-client.mjs";

// Assemble modules
import { buildSenator } from "./assemble/senator-builder.mjs";
import { writeSenators, writeMetadata } from "./assemble/output.mjs";

// Parse CLI args
const args = process.argv.slice(2);
const flags = {
  dryRun: args.includes("--dry-run"),
  fetchOnly: args.includes("--fetch-only"),
  skipFetch: args.includes("--skip-fetch"),
  senator: null,
};

const senatorIdx = args.indexOf("--senator");
if (senatorIdx !== -1 && args[senatorIdx + 1]) {
  flags.senator = args[senatorIdx + 1];
}

async function main() {
  const startTime = Date.now();

  log.info("=== MODERN PUNK DATA PIPELINE ===");
  log.info(`Mode: ${flags.dryRun ? "DRY RUN" : "LIVE"}`);
  if (flags.senator) log.info(`Single senator: ${flags.senator}`);
  if (flags.fetchOnly) log.info("Fetch only mode — no LLM analysis");
  if (flags.skipFetch) log.info("Skip fetch — using cached data");

  // Validate API keys
  if (!flags.skipFetch && !DATA_GOV_API_KEY) {
    log.error("DATA_GOV_API_KEY not set. Add it to .env file.");
    log.error("Sign up free at https://api.data.gov/signup/");
    process.exit(1);
  }
  if (!flags.fetchOnly && !GEMINI_API_KEY) {
    log.error("GEMINI_API_KEY not set. Add it to .env file.");
    log.error("Get a free key at https://aistudio.google.com/apikey");
    process.exit(1);
  }

  // ========================================
  // PHASE 1: FETCH
  // ========================================
  log.info("\n--- Phase 1: FETCH ---");

  // 1a. Fetch all current senators from Congress.gov
  log.info("Fetching senator list from Congress.gov...");
  const rawMembers = await fetchSenators();
  if (!rawMembers || rawMembers.length === 0) {
    log.error("Failed to fetch senators. Check your DATA_GOV_API_KEY.");
    process.exit(1);
  }
  log.info(`Found ${rawMembers.length} senators`);

  // 1b. Fetch detailed member info
  log.info("Fetching member details...");
  const memberDetails = {};
  for (const m of rawMembers) {
    if (m.bioguideId) {
      const detail = await fetchMemberDetail(m.bioguideId);
      if (detail) memberDetails[m.bioguideId] = detail;
    }
  }

  // ========================================
  // PHASE 2: TRANSFORM (members)
  // ========================================
  log.info("\n--- Phase 2: TRANSFORM (members) ---");
  let senators = normalizeMembers(rawMembers, memberDetails);
  log.info(`Normalized ${senators.length} senators`);

  // Filter to single senator if specified
  if (flags.senator) {
    const query = flags.senator.toLowerCase();
    senators = senators.filter(
      (s) => s.name.toLowerCase().includes(query) || s.id.toLowerCase().includes(query)
    );
    if (senators.length === 0) {
      log.error(`Senator not found: ${flags.senator}`);
      process.exit(1);
    }
    log.info(`Filtered to: ${senators.map((s) => s.name).join(", ")}`);
  }

  // 1c. Fetch key bills data
  log.info("Fetching key bills...");
  const billsData = [];
  for (const billRef of KEY_BILLS) {
    const bill = await fetchBill(billRef.congress, billRef.type, billRef.number);
    const summaries = await fetchBillSummaries(billRef.congress, billRef.type, billRef.number);
    const actions = await fetchBillActions(billRef.congress, billRef.type, billRef.number);

    // Fetch full bill text from GovInfo
    const fullText = await fetchBillText(billRef.congress, billRef.type, billRef.number);

    if (bill) {
      billsData.push({
        billId: `${billRef.type.toUpperCase()}.${billRef.number}`,
        billName: billRef.name,
        congress: billRef.congress,
        summary: summaries?.[0]?.text || bill.title || "",
        fullText: fullText || "",
        actions: actions || [],
      });
    }
  }
  log.info(`Fetched ${billsData.length}/${KEY_BILLS.length} bills`);

  // 1d. Fetch roll call votes for each bill
  log.info("Fetching roll call votes...");
  const rollCallDataMap = new Map();
  for (const bill of billsData) {
    const rollCallRef = findSenateRollCall(bill.actions);
    if (rollCallRef) {
      const rollCallData = await fetchRollCallVote(
        rollCallRef.congress,
        rollCallRef.session,
        rollCallRef.rollCallNumber
      );
      if (rollCallData) {
        rollCallDataMap.set(bill.billId, rollCallData);
      }
    }
  }
  log.info(`Roll call data fetched for ${rollCallDataMap.size}/${billsData.length} bills`);

  // 1e. Fetch FEC data for each senator
  log.info("Fetching FEC financial data...");
  const fecData = new Map();
  for (const senator of senators) {
    const candidate = await findCandidate(senator.name, senator.state);
    if (!candidate?.candidate_id) {
      log.warn(`No FEC match for ${senator.name} (${senator.state})`);
      continue;
    }

    const financials = await fetchCandidateFinancials(candidate.candidate_id);
    const committees = await fetchCandidateCommittees(candidate.candidate_id);
    const committeeId = committees?.[0]?.committee_id;

    let receipts = [];
    let pacReceipts = [];
    let aggregated = [];
    if (committeeId) {
      receipts = await fetchCommitteeReceipts(committeeId);
      pacReceipts = await fetchPACReceipts(committeeId);
      aggregated = await fetchAggregatedContributors(committeeId);
    }

    fecData.set(senator.id, { candidate, financials, receipts, pacReceipts, aggregated });
  }
  log.info(`FEC data fetched for ${fecData.size}/${senators.length} senators`);

  if (flags.fetchOnly) {
    log.info("\n=== FETCH COMPLETE (fetch-only mode) ===");
    log.info(`Cache stats: ${JSON.stringify(getCacheStats())}`);
    return;
  }

  // ========================================
  // PHASE 3: ANALYZE
  // ========================================
  log.info("\n--- Phase 3: ANALYZE ---");

  // 3a. Classify all bills using LLM
  log.info("Classifying bills...");
  const classifiedBills = await classifyAllBills(billsData);
  log.info(`Classified ${classifiedBills.length} bills`);

  // 3b. Prepare senator data for batch LLM analysis
  log.info("Preparing senator data for batch analysis...");
  const results = [];
  let successCount = 0;
  let failCount = 0;

  // Build per-senator data (finance + votes) without LLM calls
  const senatorPrepared = [];
  for (const senator of senators) {
    try {
      const fec = fecData.get(senator.id);
      const funding = fec
        ? normalizeFinance(
            fec.candidate,
            fec.financials || [],
            fec.receipts || [],
            fec.pacReceipts || [],
            fec.aggregated || []
          )
        : senator.funding;

      // Extract senator's last name for vote matching
      const nameParts = senator.name.split(/\s+/);
      const lastName = nameParts[nameParts.length - 1];

      const senatorVotes = {};
      for (const bill of classifiedBills) {
        const rollCallData = rollCallDataMap.get(bill.billId);
        if (rollCallData) {
          const vote = extractSenatorVote(
            rollCallData,
            senator.bioguideId,
            lastName,
            senator.state
          );
          if (vote) {
            senatorVotes[bill.billId] = vote;
          }
        }
      }

      const votingRecord = normalizeVotes(senator.bioguideId, classifiedBills, senatorVotes);

      senatorPrepared.push({ senator, funding, votingRecord });
    } catch (e) {
      log.error(`  ✗ Prep failed for ${senator.name}: ${e.message}`);
      failCount++;
      results.push(senator);
    }
  }

  // Batch senators into groups of 10 to stay under 20 RPD
  // (1 bill call + ~10 batch calls = ~11 total LLM calls)
  const BATCH_SIZE = 10;
  const batches = [];
  for (let i = 0; i < senatorPrepared.length; i += BATCH_SIZE) {
    batches.push(senatorPrepared.slice(i, i + BATCH_SIZE));
  }
  log.info(
    `Processing ${senatorPrepared.length} senators in ${batches.length} LLM batches (${BATCH_SIZE}/batch)...`
  );

  for (let batchIdx = 0; batchIdx < batches.length; batchIdx++) {
    const batch = batches[batchIdx];
    log.info(
      `\nBatch ${batchIdx + 1}/${batches.length}: ${batch.map((b) => b.senator.name).join(", ")}`
    );

    // Build batch input for analyzeSenatorBatch
    const batchInput = batch.map((b) => ({
      senator: b.senator,
      donors: b.funding.topDonors || [],
      keyVotes: b.votingRecord.keyVotes || [],
    }));

    const batchResults = await analyzeSenatorBatch(batchInput);

    // Process each senator's result
    for (let i = 0; i < batch.length; i++) {
      const { senator, funding, votingRecord } = batch[i];
      const analysis = batchResults[i];

      try {
        // Merge analysis into voting record
        votingRecord.keyVotes = analysis.keyVotes || votingRecord.keyVotes;

        const lobbyingMatches = analysis.lobbyingMatches || [];

        // Calculate scores using the flip-flop score from the batch
        const tempSenator = { ...senator, funding, votingRecord, lobbyingMatches };
        const corruptionScore = calculateScores(tempSenator, {
          flipFlopScore: analysis.flipFlopScore,
        });

        const nickname = analysis.punkNickname || "TBD";

        // Assemble final record
        const result = buildSenator(
          senator,
          funding,
          votingRecord,
          lobbyingMatches,
          corruptionScore,
          nickname
        );

        results.push(result);
        successCount++;
        log.info(
          `  ✓ ${senator.name}: score ${Math.round(
            corruptionScore.corporateFunding * 0.3 +
              corruptionScore.lobbyistAlignment * 0.25 +
              corruptionScore.industryConcentration * 0.2 +
              corruptionScore.flipFlopIndex * 0.15 +
              corruptionScore.revolvingDoor * 0.1
          )}/100 — "${nickname}"`
        );
      } catch (e) {
        log.error(`  ✗ Failed for ${senator.name}: ${e.message}`);
        failCount++;
        results.push(senator);
      }
    }
  }

  // ========================================
  // PHASE 4: ASSEMBLE & OUTPUT
  // ========================================
  log.info("\n--- Phase 4: OUTPUT ---");

  // Sort by state then name
  results.sort((a, b) => a.state.localeCompare(b.state) || a.name.localeCompare(b.name));

  writeSenators(results, flags.dryRun);

  const llmStats = getLLMStats();
  const cacheStats = getCacheStats();
  const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);

  writeMetadata(
    {
      senatorsProcessed: successCount,
      senatorsFailed: failCount,
      billsClassified: classifiedBills.length,
      llmStats,
      cacheStats,
      elapsedSeconds: parseFloat(elapsed),
    },
    flags.dryRun
  );

  log.info("\n=== PIPELINE COMPLETE ===");
  log.info(`Senators: ${successCount} success, ${failCount} failed`);
  log.info(`Bills classified: ${classifiedBills.length}`);
  log.info(`LLM: ${llmStats.totalCalls} calls, ${llmStats.estimatedCost}`);
  log.info(`Cache: ${cacheStats.hits} hits, ${cacheStats.misses} misses (${cacheStats.hitRate})`);
  log.info(`Time: ${elapsed}s`);
}

main().catch((e) => {
  log.error("Pipeline failed:", e);
  process.exit(1);
});
