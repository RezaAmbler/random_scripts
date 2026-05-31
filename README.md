# random_scripts

A personal collection of standalone scripts, organized into folders by category.

## Categories

- **`qnap/`** — Scripts for the QNAP NAS.
  - `find_duplicates.py` — find duplicate files by size + MD5 (parallel scan + hashing), with optional safe deletion (Python 2.7).
- **`network/`** — Networking and routing diagnostics.
  - `router-views-lookup-mass.py` — BGP prefix visibility checker across route servers.
  - `whois-checker.py` — WHOIS lookup + parsing for IP prefixes.
  - `spamhaus-check.sh` — check IPs/CIDRs against the Spamhaus ZEN DNSBL.
  - `domain-info.sh` — summarize a domain's A/MX/NS records + owning org.
  - `network-imix-perf-test.sh` — iperf3 IMIX traffic / performance tester.
- **`dns/`** — DNS administration.
  - `add-dns-from-csv.ps1` — bulk-create AD DNS A/PTR records from a CSV.
  - `export-windows-dns-to-cli53.ps1` — emit cli53 commands to migrate Windows DNS to Route 53.

Each script is self-contained. See the script's header comment for usage and
dependencies. New scripts go into a category folder (see `CLAUDE.md`).
