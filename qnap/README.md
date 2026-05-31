# qnap/

Scripts for the QNAP NAS. Most target the stock **Python 2.7** interpreter that
ships with QNAP (QTS) and use the standard library only — no `pip install` needed.

## Scripts

### `find_duplicates.py`
Finds duplicate files under one or more directories using a cheap-to-expensive
funnel: group by size, eliminate non-matches with a head+tail partial MD5, then
confirm the rest with a full MD5. Skips QNAP metadata dirs (`@...`, `.@__thumb`,
`lost+found`) and de-dups hardlinks/duplicate paths by inode.

```sh
# Basic scan
python find_duplicates.py /share/CACHEDEV1_DATA/Multimedia

# Multiple paths + save a report
python find_duplicates.py /share/CACHEDEV1_DATA/Multimedia /share/CACHEDEV1_DATA/Download -o report.txt
```

#### How it works — the phases

Each phase narrows the set so the next, more expensive one does less work:

```
   every file in the tree
        │  Phase 1 · Scan pass 1   (parallel walk, --scan-workers)
        │  tally a {size: count} table — no paths kept yet (bounds memory)
        ▼
   files whose size is shared by >=2 files
        │  Phase 1 · Scan pass 2   (parallel walk, --scan-workers)
        │  re-walk and collect paths for those colliding sizes only
        ▼
   size-collision candidates
        │  Phase 2 · Pre-filter    (--scan-workers; only groups of >2)
        │  MD5 of just the first + last 64 KB of each file
        ▼  drops same-size-but-different files for ~128 KB of reads each
   pre-filter survivors
        │  Phase 3 · Full hash     (-j / --workers; keep LOW on HDDs)
        │  read each survivor end-to-end, full MD5, group by digest
        ▼
   byte-identical groups  ──►  report  (+ optional safe deletion, --delete)
```

**Why the pre-filter matters.** Two files of the same size are only *suspects* —
confirming them normally means reading both in full (each could be hundreds of
MB). The pre-filter instead reads only the **first and last 64 KB** and hashes
that; if those differ, the files cannot be identical and are dropped without ever
touching the bulk. Sampling the **tail as well as the head** catches files that
share a container header but diverge later (common with media: MP4/MKV that share
metadata atoms). Rejecting a non-match this way costs ~128 KB instead of, say,
600 MB — roughly a 5000x saving. Groups of exactly two skip the pre-filter (it
can't shrink a pair; you'd have to full-hash both anyway to confirm).

The script prints a summary after **SCAN COMPLETE** and **PRE-FILTER COMPLETE**
(counts, data volume, survivors) and reports each phase's wall-clock time — handy
for tuning the knobs below.

#### Performance & concurrency

The phases have **opposite** I/O profiles, so they get separate thread knobs:

| Phase | I/O pattern | Wants | Knob |
|-------|-------------|-------|------|
| Scan walk + pre-filter | many **small, random** reads (inode lookups, 64 KB head/tail of scattered files) | **high** concurrency — a deep queue lets the array reorder seeks / use all spindles | `--scan-workers` (default 8; try 16) |
| Full hash | **large sequential** reads (whole files) | **low** concurrency on spinning disks — too many streams make the heads seek-thrash | `-j` / `--workers` (default 4; use **1–2** on HDDs) |

It's I/O-latency-bound, not CPU-bound, so these knobs (not the CPU) set the pace.
Measured on a spinning QNAP RAID (`md1`): the full hash at `-j 8` thrashed down to
~17 MB/s (reads chopped to ~70 KB), while `-j 2` read sequentially at
~300–500 MB/s — about **20x faster** — and the pre-filter, starved at a low `-j`,
runs fast on `--scan-workers 8`. **Rule of thumb on HDDs: `--scan-workers` high,
`-j` low.** On SSD or SSD-cached volumes, raise `-j` too. Tune empirically using
the per-phase times the script prints.

Memory stays bounded regardless of tree size: the two-pass scan tallies sizes
first and only keeps paths for size-colliding candidates, so peak RAM scales with
collisions, not with the total file count. `Ctrl+C` cleanly aborts any phase
(nothing is deleted unless you pass `--apply`).

Exit codes: `0` = no duplicates (or `--zero-exit`), `1` = duplicates found
(handy as a cron signal), `2` = no valid paths. See the script's header docstring
for the full option list (`--min-size`, `--include-metadata`, `--follow-symlinks`,
`--verbose`, `--no-progress`, `--workers`, `--scan-workers`).

#### Deleting duplicates
By default the script only reports. Add `--delete` to plan removals — it keeps
**one** copy per group and never deletes the last copy. `--delete` alone is a
**dry run** (prints the plan + reclaimable space, deletes nothing):

```sh
# 1) Preview, keeping the oldest copy of each (default). A reusable JSON plan
#    is auto-saved (find_dupes_plan_<timestamp>.json) so you don't rescan later.
python find_duplicates.py /share/CACHEDEV1_DATA/Multimedia --delete

# 2) Apply that exact plan WITHOUT re-scanning the whole tree again:
python find_duplicates.py --from-plan find_dupes_plan_20260530_154635.json --apply --verify

# Or: actually delete right after the scan (prompts y/N; --yes skips it)
python find_duplicates.py /share/... --delete --apply

# Or: export a reviewable rm-script and run it yourself
python find_duplicates.py /share/... --delete --delete-script cleanup.sh
```

- `--keep {oldest,newest,shortest,first}` — which copy survives (default
  `oldest` by mtime; `shortest` = shortest path, `first` = first alphabetical).
- **Plan reuse:** a dry run writes a JSON plan (override path with `--save-plan
  FILE`, suppress with `--no-save-plan`). `--from-plan FILE` re-applies it with no
  scan; add `--apply` to delete and `--verify` to re-hash first (recommended for
  older plans). The rm-script (`--delete-script`) is the manual alternative — run
  it with `sh`; it carries no hashes, so prefer `--from-plan` for safe re-apply.
- `--apply` actually deletes (confirmation prompt unless `--yes`); `--verify`
  re-hashes each file right before deleting and skips it if its content changed.
- Safety: dry-run by default, one copy always kept, a missing survivor skips the
  whole group, and per-file errors are counted (not fatal). Hardlinks/symlinks
  are never split — the scan already de-dups by inode.
