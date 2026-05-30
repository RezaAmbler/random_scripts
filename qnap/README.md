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

Exit codes: `0` = no duplicates (or `--zero-exit`), `1` = duplicates found
(handy as a cron signal), `2` = no valid paths. See the script's header docstring
for the full option list (`--min-size`, `--include-metadata`, `--follow-symlinks`,
`--verbose`, `--no-progress`).

#### Deleting duplicates
By default the script only reports. Add `--delete` to plan removals — it keeps
**one** copy per group and never deletes the last copy. `--delete` alone is a
**dry run** (prints the plan + reclaimable space, deletes nothing):

```sh
# Preview what would be removed, keeping the oldest copy of each (default)
python find_duplicates.py /share/CACHEDEV1_DATA/Multimedia --delete

# Export a reviewable rm-script instead of deleting in-process
python find_duplicates.py /share/... --delete --delete-script cleanup.sh

# Actually delete (prompts y/N first; --yes skips the prompt for cron)
python find_duplicates.py /share/... --delete --apply
python find_duplicates.py /share/... --delete --apply --yes --verify
```

- `--keep {oldest,newest,shortest,first}` — which copy survives (default
  `oldest` by mtime; `shortest` = shortest path, `first` = first alphabetical).
- `--apply` actually deletes (with a confirmation prompt unless `--yes`);
  `--verify` re-hashes each file right before deleting and skips it if its
  content changed since the scan.
- Safety: dry-run by default, one copy always kept, a missing survivor skips the
  whole group, and per-file errors are counted (not fatal). Hardlinks/symlinks
  are never split — the scan already de-dups by inode.
