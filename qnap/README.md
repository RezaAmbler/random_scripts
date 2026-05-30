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
