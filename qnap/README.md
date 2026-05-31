# qnap/

Scripts for the QNAP NAS. Most target the stock **Python 2.7** interpreter that
ships with QNAP (QTS) and use the standard library only — no `pip install` needed.

## Scripts

### `find_duplicates.py`
Finds duplicate files under one or more directories. Groups by size, pre-filters
with a head+tail partial MD5, then confirms with a full MD5. Skips QNAP metadata
dirs (`@...`, `.@__thumb`, `lost+found`) and hardlinks/duplicate paths by inode.

```sh
# Basic scan
python find_duplicates.py /share/CACHEDEV1_DATA/Multimedia

# Multiple paths + save a report
python find_duplicates.py /share/CACHEDEV1_DATA/Multimedia /share/CACHEDEV1_DATA/Download -o report.txt
```

Both phases are multithreaded and the I/O is latency-bound, so on an idle
multi-disk array more threads = faster: `--workers N` (hashing, default 4) and
`--scan-workers N` (the directory/stat walk, default 8). The walk is metadata-
bound so it tolerates higher concurrency than hashing; the scan's wall-clock
time is printed in the summary. Use `--workers 1` for a single spinning disk
where parallel reads thrash. Memory stays bounded regardless of tree size (the
scan tallies sizes first, then keeps paths only for size-colliding candidates).

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
