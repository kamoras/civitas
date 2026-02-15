import { writeFileSync } from "fs";
import { join } from "path";
import { log } from "../config.mjs";

const OUTPUT_DIR = new URL("../../../src/data", import.meta.url).pathname;

/**
 * Write the final senators.json file.
 * @param {Array} senators - Validated Senator records
 * @param {boolean} dryRun - If true, don't write, just log
 */
export function writeSenators(senators, dryRun = false) {
  const outputPath = join(OUTPUT_DIR, "senators.json");
  const json = JSON.stringify(senators, null, 2);

  if (dryRun) {
    log.info(`[DRY RUN] Would write ${senators.length} senators to ${outputPath}`);
    log.info(`[DRY RUN] Output size: ${(json.length / 1024).toFixed(1)} KB`);
    return;
  }

  writeFileSync(outputPath, json);
  log.info(
    `Wrote ${senators.length} senators to ${outputPath} (${(json.length / 1024).toFixed(1)} KB)`
  );
}

/**
 * Write pipeline run metadata.
 * @param {Object} metadata - Run info
 */
export function writeMetadata(metadata, dryRun = false) {
  const outputPath = join(OUTPUT_DIR, "pipeline-metadata.json");
  const data = {
    lastRun: new Date().toISOString(),
    ...metadata,
  };

  if (dryRun) {
    log.info(`[DRY RUN] Pipeline metadata:`, JSON.stringify(data, null, 2));
    return;
  }

  writeFileSync(outputPath, JSON.stringify(data, null, 2));
  log.info(`Wrote pipeline metadata to ${outputPath}`);
}
