# Score Audit — Algorithm Accuracy & Data Quality Evaluation

Runs a full diagnostic on scoring algorithm accuracy and data quality. Use this whenever:
- The nightly pipeline completes and you want to verify output quality
- A scoring algorithm change is deployed and needs validation
- A new data source is added and you want to assess its impact
- A politician's score seems wrong and you want to investigate

---

## Step 1 — Pull the Current Score Distribution

```bash
docker exec mp-backend-blue python3 - <<'EOF'
import sqlite3, statistics
conn = sqlite3.connect('/data/modern-punk.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("""
SELECT name, state, party,
  round(score_funding_independence,1) fi,
  round(score_promise_persistence,1) pp,
  round(score_independent_voting,1) iv,
  round(score_funding_diversity,1) fd,
  round(score_legislative_effectiveness,1) le,
  round((score_funding_independence*0.25 + score_promise_persistence*0.25
       + score_independent_voting*0.25 + score_funding_diversity*0.25), 1) overall,
  round(total_from_pacs/nullif(total_raised,0)*100, 2) pac_pct,
  round(total_raised/1e6, 2) raised_m
FROM senators ORDER BY overall DESC
""")
rows = cur.fetchall()

print(f"{'Name':<28} {'ST':>2} {'P':>1}  {'FI':>4} {'PP':>4} {'IV':>4} {'FD':>4} {'LE':>4}  {'OVR':>4}  {'PAC%':>5} {'$M':>6}")
print("-" * 90)
for r in rows:
    print(f"{r['name']:<28} {r['state']:>2} {r['party']:>1}  {r['fi'] or 0:>4} {r['pp'] or 0:>4} {r['iv'] or 0:>4} {r['fd'] or 0:>4} {r['le'] or 0:>4}  {r['overall'] or 0:>4}  {r['pac_pct'] or 0:>5} {r['raised_m'] or 0:>6}")

for dim in ['fi','pp','iv','fd','le','overall']:
    vals = [r[dim] or 0 for r in rows]
    print(f"\n{dim.upper()}: min={min(vals)} max={max(vals)} mean={round(statistics.mean(vals),1)} stdev={round(statistics.stdev(vals),1)} median={statistics.median(vals)}")

conn.close()
EOF
```

**What to look for:**
- `stdev` should be 10–20 for each dimension. If stdev < 8, scores are too compressed — the formula needs recalibration or more data.
- `mean` should be near 50. If it's consistently above 65, there's an upward bias (likely a bad default or missing data treated as positive).
- Any dimension where min > 35 or max < 65 has no meaningful spread — investigate why.

---

## Step 2 — Ground Truth Spot Check

Compare specific senators against known facts. These should be stable reference points across any algorithm version:

```bash
docker exec mp-backend-blue python3 - <<'EOF'
import sqlite3
conn = sqlite3.connect('/data/modern-punk.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

GROUND_TRUTH = [
    # (name_fragment, expected_iv_range, expected_fi_range, note)
    ("Collins",    (75, 100), (70, 100), "Known party crosser, grassroots ME fundraising"),
    ("Murkowski",  (75, 100), (70, 100), "Frequent party breaks, AK independent streak"),
    ("Sanders",    (40, 75),  (80, 100), "Independent, strong small-donor base"),
    ("Warren",     (40, 70),  (80, 100), "Rejected corporate PAC money explicitly"),
    ("Cruz",       (20, 55),  (50, 95),  "High party loyalty, Texas energy donors"),
    ("McConnell",  (20, 55),  (20, 65),  "Senate GOP leader, heavy outside spending"),
    ("Paul",       (65, 95),  (70, 100), "Libertarian crosser, small-dollar base"),
    ("Klobuchar",  (40, 70),  (35, 65),  "Moderate D, mid-range PAC reliance"),
]

print(f"{'Senator':<15} {'IV':>4} {'FI':>4}  IV-OK  FI-OK  Note")
print("-" * 75)
for frag, iv_range, fi_range, note in GROUND_TRUTH:
    cur.execute("SELECT name, score_independent_voting iv, score_funding_independence fi FROM senators WHERE name LIKE ?", (f'%{frag}%',))
    row = cur.fetchone()
    if not row:
        print(f"  {frag:<13} NOT FOUND")
        continue
    iv_ok = "✓" if iv_range[0] <= (row['iv'] or 0) <= iv_range[1] else f"✗ ({iv_range[0]}-{iv_range[1]})"
    fi_ok = "✓" if fi_range[0] <= (row['fi'] or 0) <= fi_range[1] else f"✗ ({fi_range[0]}-{fi_range[1]})"
    print(f"  {row['name']:<15} {row['iv'] or 0:>4} {row['fi'] or 0:>4}  {iv_ok:<6} {fi_ok:<6} {note}")

conn.close()
EOF
```

**What to look for:**
- Any `✗` result means a known-bad senator scores well on that dimension, or a known-good senator scores poorly.
- Collins and Murkowski should always rank top-5 on IV. If they don't, the party-break calculation is off.
- McConnell FI should be notably below Sanders FI. If they're within 15 points of each other, the formula isn't discriminating.

---

## Step 3 — Data Quality Assessment

```bash
docker exec mp-backend-blue python3 - <<'EOF'
import sqlite3
conn = sqlite3.connect('/data/modern-punk.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Coverage rates
cur.execute("SELECT count(*) n FROM senators"); total = cur.fetchone()['n']
checks = [
    ("Has FEC funding data",   "total_raised > 0"),
    ("Has vote record",        "EXISTS(SELECT 1 FROM key_votes kv WHERE kv.senator_id=s.id)"),
    ("Has campaign promises",  "EXISTS(SELECT 1 FROM campaign_promises cp WHERE cp.senator_id=s.id)"),
    ("Has lobbying matches",   "EXISTS(SELECT 1 FROM lobbying_matches lm WHERE lm.senator_id=s.id)"),
    ("Has industry breakdown", "EXISTS(SELECT 1 FROM industry_donations id2 WHERE id2.senator_id=s.id)"),
    ("Has outside spending",   "total_raised > 0"),  # proxy — check actual field if column exists
]

print(f"DATA COVERAGE ({total} senators total)")
print("-" * 50)
for label, condition in checks:
    try:
        cur.execute(f"SELECT count(*) n FROM senators s WHERE {condition}")
        n = cur.fetchone()['n']
        pct = round(n / total * 100)
        status = "✓" if pct >= 90 else ("⚠" if pct >= 70 else "✗")
        print(f"  {status} {label:<30} {n:>3}/{total} ({pct}%)")
    except Exception as e:
        print(f"  ? {label:<30} ERROR: {e}")

# Senators defaulting to 50 on multiple dimensions (data desert)
cur.execute("""
SELECT name, state,
  score_funding_independence fi, score_promise_persistence pp,
  score_independent_voting iv, score_funding_diversity fd
FROM senators
WHERE abs(score_funding_independence - 50) < 2
  AND abs(score_promise_persistence - 50) < 2
  AND abs(score_independent_voting - 50) < 2
""")
deserts = cur.fetchall()
if deserts:
    print(f"\n⚠ DATA DESERTS (3+ dimensions defaulting near 50): {len(deserts)}")
    for r in deserts:
        print(f"  {r['name']} ({r['state']}): FI={r['fi']} PP={r['pp']} IV={r['iv']} FD={r['fd']}")
else:
    print("\n✓ No data deserts found")

# Vote count distribution
cur.execute("""
SELECT
  count(case when vote_count = 0 then 1 end) no_votes,
  count(case when vote_count between 1 and 50 then 1 end) low,
  count(case when vote_count between 51 and 200 then 1 end) mid,
  count(case when vote_count > 200 then 1 end) high
FROM (SELECT senator_id, count(*) vote_count FROM key_votes GROUP BY senator_id) t
RIGHT JOIN senators s ON t.senator_id = s.id
""")
vd = dict(cur.fetchone())
print(f"\nVOTE DATA DISTRIBUTION")
print(f"  No votes:    {vd.get('no_votes',0):>3} senators (IV defaults to 50)")
print(f"  1-50 votes:  {vd.get('low',0):>3} senators (sparse)")
print(f"  51-200:      {vd.get('mid',0):>3} senators (adequate)")
print(f"  200+ votes:  {vd.get('high',0):>3} senators (good)")

# Promise coverage
cur.execute("""
SELECT
  count(distinct senator_id) with_promises,
  count(case when alignment != 'unclear' then 1 end) evaluable,
  count(*) total_promises
FROM campaign_promises
""")
pd = dict(cur.fetchone())
print(f"\nPROMISE DATA")
print(f"  Senators with promises: {pd.get('with_promises',0)}")
print(f"  Total promises tracked: {pd.get('total_promises',0)}")
print(f"  Evaluable (not unclear): {pd.get('evaluable',0)}")

conn.close()
EOF
```

**What to look for:**
- Any coverage below 70% (✗) means the pipeline has a data fetch problem — investigate before trusting scores.
- Data deserts (3+ dimensions at 50) indicate senators where no data source is working — could be new senators, name-matching failures, or API gaps.
- If >20 senators have zero votes, the vote normalization is broken.
- If evaluable promises < 30% of total, the LLM is classifying too many as "unclear" — may need prompt tuning.

---

## Step 4 — Dimension Correlation Analysis

High correlation between two dimensions means they're measuring the same thing (a design flaw):

```bash
docker exec mp-backend-blue python3 - <<'EOF'
import sqlite3, statistics, math
conn = sqlite3.connect('/data/modern-punk.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("""
SELECT score_funding_independence fi, score_promise_persistence pp,
       score_independent_voting iv, score_funding_diversity fd,
       score_legislative_effectiveness le
FROM senators WHERE total_raised > 0
""")
rows = [dict(r) for r in cur.fetchall()]

dims = ['fi', 'pp', 'iv', 'fd', 'le']
def corr(xs, ys):
    n = len(xs)
    mx, my = sum(xs)/n, sum(ys)/n
    num = sum((x-mx)*(y-my) for x,y in zip(xs,ys))
    den = math.sqrt(sum((x-mx)**2 for x in xs) * sum((y-my)**2 for y in ys))
    return round(num/den, 3) if den else 0

print("DIMENSION CORRELATION MATRIX (target: all |r| < 0.40)")
print(f"{'':>4}", end="")
for d in dims: print(f"  {d.upper():>5}", end="")
print()
for d1 in dims:
    print(f"{d1.upper():>4}", end="")
    for d2 in dims:
        xs = [r[d1] for r in rows]
        ys = [r[d2] for r in rows]
        r = corr(xs, ys)
        flag = " !" if d1 != d2 and abs(r) > 0.40 else "  "
        print(f"{flag}{r:>5}", end="")
    print()

print("\n! = correlation above 0.40, indicating possible dimension overlap")
conn.close()
EOF
```

**What to look for:**
- Any off-diagonal correlation above 0.40 (absolute value) is a design problem.
- PP×IV correlation above 0.4 means the PP fallback is still using voting data (check score_calculator.py).
- FI×FD correlation above 0.5 is expected (both measure funding quality) but shouldn't be above 0.7.
- IV×FI correlation above 0.4 suggests the donor independence component is driving both.

---

## Step 5 — Outlier Investigation

For any senator whose score seems wrong, run this deep-dive:

```bash
# Replace SENATOR_NAME with e.g. "McConnell" or "Collins"
SENATOR_NAME="McConnell"

docker exec mp-backend-blue python3 - <<EOF
import sqlite3, json
conn = sqlite3.connect('/data/modern-punk.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("SELECT * FROM senators WHERE name LIKE ?", (f'%${SENATOR_NAME}%',))
s = dict(cur.fetchone() or {})
if not s:
    print("Not found"); exit()

print(f"=== {s['name']} ({s['state']}-{s['party']}) ===")
print(f"FI={s['score_funding_independence']} PP={s['score_promise_persistence']} IV={s['score_independent_voting']} FD={s['score_funding_diversity']} LE={s['score_legislative_effectiveness']}")
print(f"Total raised: \${s['total_raised']:,.0f} | PAC total: \${s['total_from_pacs']:,.0f} ({round(s['total_from_pacs']/max(s['total_raised'],1)*100,1)}%)")

# Vote breakdown
cur.execute("""
SELECT count(*) total,
  sum(case when voted_with_party=1 then 1 else 0 end) with_party,
  sum(case when voted_with_party=0 then 1 else 0 end) against_party
FROM key_votes WHERE senator_id=?
""", (s['id'],))
v = dict(cur.fetchone())
print(f"Votes: {v['total']} total | {v['with_party']} with party | {v['against_party']} against party ({round(v['against_party']/max(v['total'],1)*100,1)}%)")

# Promise breakdown
cur.execute("""
SELECT alignment, count(*) n FROM campaign_promises
WHERE senator_id=? GROUP BY alignment
""", (s['id'],))
print("Promises:", {r['alignment']: r['n'] for r in cur.fetchall()})

# Top donors
cur.execute("SELECT name, total, type, industry FROM donors WHERE senator_id=? ORDER BY total DESC LIMIT 5", (s['id'],))
print("Top donors:", [dict(r) for r in cur.fetchall()])

# Lobbying
cur.execute("SELECT count(*) n, sum(case when senator_vote_aligned=1 then 1 else 0 end) aligned FROM lobbying_matches WHERE senator_id=?", (s['id'],))
lm = dict(cur.fetchone())
print(f"Lobbying matches: {lm['n']} total, {lm['aligned']} aligned")

conn.close()
EOF
```

---

## Step 6 — House Representative Data Check

```bash
docker exec mp-backend-blue python3 - <<'EOF'
import sqlite3
conn = sqlite3.connect('/data/modern-punk.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("SELECT count(*) n FROM representatives"); total = cur.fetchone()['n']
cur.execute("SELECT count(*) n FROM representatives WHERE total_raised > 0"); finance = cur.fetchone()['n']
cur.execute("SELECT count(*) n FROM rep_key_votes"); votes = cur.fetchone()['n']
cur.execute("SELECT count(*) n FROM rep_donors"); donors = cur.fetchone()['n']

print(f"House representatives: {total}")
print(f"  With FEC data:  {finance} ({round(finance/max(total,1)*100)}%)")
print(f"  Key votes:      {votes}")
print(f"  Donor records:  {donors}")

if total > 0:
    cur.execute("""
    SELECT state, count(*) n,
      round(avg(score_independent_voting),1) avg_iv,
      round(avg(score_funding_independence),1) avg_fi
    FROM representatives GROUP BY state ORDER BY n DESC LIMIT 10
    """)
    print("\nTop states by rep count:")
    for r in cur.fetchall():
        print(f"  {r['state']}: {r['n']} reps, avg IV={r['avg_iv']} FI={r['avg_fi']}")

conn.close()
EOF
```

---

## Step 7 — Score History Trend Check

After multiple pipeline runs, verify that scores are stable (not oscillating wildly):

```bash
docker exec mp-backend-blue python3 - <<'EOF'
import sqlite3
conn = sqlite3.connect('/data/modern-punk.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("SELECT count(distinct date) n_dates, count(distinct entity_id) n_entities FROM score_snapshots")
snap = dict(cur.fetchone())
print(f"Score snapshots: {snap['n_dates']} pipeline runs × {snap['n_entities']} senators")

# Senators with high score variance (oscillating)
cur.execute("""
SELECT entity_id,
  max(overall_score) - min(overall_score) as range_,
  count(*) n_snaps,
  round(avg(overall_score), 1) avg_score
FROM score_snapshots
WHERE entity_type = 'senator'
GROUP BY entity_id
HAVING n_snaps >= 3 AND range_ > 15
ORDER BY range_ DESC
LIMIT 10
""")
rows = cur.fetchall()
if rows:
    print("\n⚠ High-variance senators (score swings >15 points across runs):")
    for r in rows:
        cur.execute("SELECT name FROM senators WHERE id=?", (r['entity_id'],))
        name = (cur.fetchone() or {}).get('name', r['entity_id'])
        print(f"  {name}: range={r['range_']:.1f} over {r['n_snaps']} runs (avg={r['avg_score']})")
else:
    print("✓ No high-variance senators found")

conn.close()
EOF
```

**What to look for:**
- Senators with >15 point score swings across runs indicate that their data is inconsistently fetched (e.g., vote matching failures on some runs) or that the formula is sensitive to data order. Investigate the specific senators.
- If most senators have swings <5 points, the system is stable.

---

## Step 8 — Algorithm Change Impact Assessment

Run this BEFORE and AFTER any algorithm change to measure impact:

```bash
docker exec mp-backend-blue python3 - <<'EOF'
import sqlite3, statistics
conn = sqlite3.connect('/data/modern-punk.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("""
SELECT
  entity_id,
  date,
  overall_score,
  score_1 fi, score_2 pp, score_3 iv, score_4 fd, score_5 le
FROM score_snapshots
WHERE entity_type = 'senator'
ORDER BY entity_id, date
""")
rows = cur.fetchall()

# Group by senator, compare latest to previous
by_senator = {}
for r in rows:
    by_senator.setdefault(r['entity_id'], []).append(dict(r))

shifts = []
for sid, snaps in by_senator.items():
    if len(snaps) < 2:
        continue
    prev, curr = snaps[-2]['overall_score'], snaps[-1]['overall_score']
    shifts.append((curr - prev, sid, curr, prev))

if not shifts:
    print("Only one pipeline run in history — run again after deploying changes")
else:
    shifts.sort(reverse=True)
    print("LARGEST SCORE CHANGES (last 2 pipeline runs):")
    print(f"{'Senator':<25} {'Prev':>5} {'Curr':>5} {'Δ':>5}")
    print("-" * 45)
    for delta, sid, curr, prev in shifts[:10] + shifts[-5:]:
        cur.execute("SELECT name FROM senators WHERE id=?", (sid,))
        name = (cur.fetchone() or {}).get('name', sid)
        arrow = "↑" if delta > 0 else "↓"
        print(f"{name:<25} {prev:>5.1f} {curr:>5.1f} {arrow}{abs(delta):>4.1f}")
    
    all_deltas = [s[0] for s in shifts]
    print(f"\nMean shift: {statistics.mean(all_deltas):+.2f}")
    print(f"Median shift: {statistics.median(all_deltas):+.2f}")
    print(f"Max gain: +{max(all_deltas):.1f}, Max loss: {min(all_deltas):.1f}")

conn.close()
EOF
```

---

## Iteration Decision Framework

After running the audit, use this framework to decide what to change:

| Finding | Likely cause | Fix |
|---|---|---|
| stdev < 8 on any dimension | Formula too narrow, or defaults dominate | Recalibrate multipliers; check default values |
| mean > 65 on any dimension | Missing data treated as positive | Change "no data" default from positive to neutral (50) |
| PP×IV correlation > 0.4 | PP fallback using vote data | Remove voting fallback from PP; use 50 |
| FI > 85 for high-fundraising senators | Outside spending not captured | Check outsideSpendingFor field; verify FEC Schedule E fetch |
| Ground truth check ✗ on Collins/Murkowski IV | Vote matching broken | Check key_votes table; rerun normalize_votes |
| >20% senators in data desert | API fetch failure | Check API cache, rate limits, name matching |
| High score variance (>15 pts) on specific senator | Inconsistent vote/FEC matching | Add name normalization or use bioguide_id as primary key |
| LE scores all below 50 | Advancement threshold too high | Lower threshold (currently 5%; Senate average ~3%) |

## Algorithm Version Log

Track changes here to maintain an auditable history:

| Date | Change | Rationale | Impact |
|---|---|---|---|
| 2026-05 v3 | FI: reweighted PAC 50→30%, concentration 50→70% | Direct PAC% undercounts senior senators; concentration more reliable | McConnell FI reduced; better spread |
| 2026-05 v3 | FI: added outsideSpendingFor (Schedule E, half-weight) | Super PAC spending is key influence signal missed by direct PAC% | High-outside-spending senators penalized |
| 2026-05 v3 | PP: removed voting fallback; neutral prior = 50 | PP×IV correlation flaw; circular dependency on same vote data | PP variance reduced; dimensions decoupled |
| 2026-05 v3 | IV: fundraising-scaled donor default (75→50 at $50M+) | High fundraising without lobbying = data gap, not independence | McConnell/Graham IV reduced |
| 2026-05 v3 | LE: advancement threshold 10→5%; volume ceiling log2(200)→log2(100) | 10% threshold made median senators score below 50; unreachable ceiling | LE scores spread more evenly |
| 2026-05 fix | House pipeline: `parts[1].isdigit()` guard | `int("")` crash on bills with empty number prevented all rep data | 435 reps now populate correctly |
