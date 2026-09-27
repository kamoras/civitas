# Congress record

What each chamber did each day, for the `/congress` reports. Runs every half
hour at :10 and :40 UTC (`scheduler._congress_activity_sync`,
`backend/app/pipeline/congress_activity.py`).

```mermaid
flowchart TB
    TICK(["Half-hourly, :10 / :40"]) --> RC & FL & DD

    RC["<b>Roll calls</b><br/>senate.gov vote XML · clerk.house.gov/evs<br/>from the highest stored number up<br/>250 per chamber per run"]
    RC --> RCDB[("roll_calls<br/>roll_call_positions<br/>every member's position")]

    FL["<b>Floor logs</b>, today and yesterday (Eastern)<br/>House Clerk FloorSummary/YYYYMMDD.xml<br/>Senate MM_DD_YYYY_Senate_Floor.xml"]
    FL --> DAY

    DD["<b>Daily Digest</b> (GPO, next day)<br/>GovInfo CREC granules, class DAILYDIGEST<br/>last 7 days until final + 20-day back-fill batch"]
    DD --> DAY

    DAY[("congress_days (one per chamber per day)<br/>congress_events (passed · failed · reported ·<br/>confirmed · committee · floor)")]
```

**The Digest's own words.** Every event keeps the record's wording. Parsing
only decides which heading an entry sits under ("Measures Passed:",
"Nominations Confirmed:", a House measure's own heading, "Suspensions:") and
pulls out the bill number. Entries split at page-reference lines and at
column-0 heading lines after a finished sentence; a two-space indent always
opens a sub-item, because continuation lines are at column 0
(`app/pipeline/fetch/daily_digest.py`, tested against real issues in
`backend/tests/fixtures/daily_digest`).

**Live, then final.** A chamber's floor log fills its day while it meets;
the Digest replaces the row (`is_final`) the next day. The floor log's timed
entries stay beside the Digest's, since only the House log has times.

**Absent is not failed.** A 404, or senate.gov's redirect of a missing file
to an HTML "not found" page, means the source has nothing for that day (the
chamber did not meet, the vote does not exist yet). Any other failure writes
nothing: a Digest day is never made final from part of its granules, and the
back-fill cursor stops before a failed day so the next run retries it. Each
run's per-source outcome is stored (`api_cache`, tier `congress`, key
`congress-sync-last-run`).

**Reports.** `app/services/congress_service.py` builds the day, week and
month reports from these rows: counts, "passed both chambers" (a measure
passed by the chamber it did not start in; simple resolutions never count),
the three closest votes, and a one-line summary filled from counts by
template. Served at `/api/congress/{latest, day/…, week/…, month/…}`; one roll
call with every member's position at `/api/congress/votes/…`. A bill's full
record for its page, any bill, at `/api/bills/{id}/record`
(`app/services/bill_record.py`).

**Pages.** `/congress` (latest day), `/congress/{date}`, `/congress/week/{date}`,
`/congress/month/{YYYY-MM}` render these reports on the server
(`frontend/src/components/congress/`); `/congress/bills` is the in-motion list
and `/congress/bills/{id}` any bill's page, whose vote panel loads one roll
call's members at a time. `/bills` and `/bills/:id` redirect permanently.

**Bluesky.** After each sync run, `analyze/congress_bluesky.py` posts the most
recent session day whose record the Digest has made final, if it is from the
last three days and not already posted: the day report's sentence and the
passed bills' numbers (never titles), linking to `/congress/{date}`.

**Bill ids** are the site's (`S.3257`, `HCONRES.89`) whichever spelling the
source used: the Record's "H. Con. Res. 89", the House roll call's
"H CON RES 89", the Senate log's "H.Con.Res. 89".
