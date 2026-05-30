# random_scripts

A personal collection of standalone scripts, organized into folders by category.

## Categories

- **`qnap/`** — Scripts for the QNAP NAS.
  - `find_duplicates.py` — find duplicate files by size + MD5 (Python 2.7).
- **`network/`** — Networking and routing diagnostics.
  - `router-views-lookup-mass.py` — BGP prefix visibility checker across route servers.
  - `whois-checker.py` — WHOIS lookup + parsing for IP prefixes.
  - `network-imix-perf-test.sh` — iperf3 IMIX traffic / performance tester.
- **`dns/`** — DNS administration.
  - `add-dns-from-csv.ps1` — bulk-create AD DNS A/PTR records from a CSV.

Each script is self-contained. See the script's header comment for usage and
dependencies. New scripts go into a category folder (see `CLAUDE.md`).
