# network/

Networking and routing diagnostic tools.

## Scripts

### `router-views-lookup-mass.py`
BGP prefix visibility checker. Queries multiple public route-server / route-views
hosts (over SSH or telnet) to analyze how a prefix propagates and which transit
providers (e.g. Lumen/Zayo/AT&T) carry it across the global routing table.

- **Python 3**, third-party deps: `pyyaml`, and `asyncssh` and/or `telnetlib3`
  for the connection transports (`pip install pyyaml asyncssh telnetlib3`).
- Route servers are defined in a YAML config (default `route_servers.yaml`); the
  script writes a sample config on first run if none exists.
- Validates prefixes (v4/v6) before querying and reports per-prefix progress
  (`[N/total] Querying … (Xs elapsed)`) plus a completion line.

```sh
python3 router-views-lookup-mass.py --prefix 8.8.8.0/24 --config route_servers.yaml
python3 router-views-lookup-mass.py -f prefixes.txt --save results.txt
```

### `whois-checker.py`
Looks up WHOIS records for a list of IP prefixes via the system `whois` command
and parses out the interesting fields (OrgName, NetName, CIDR, etc.) into a table,
writing the full raw WHOIS text to an output file.

- **Python 3**, stdlib only. Requires the `whois` CLI to be installed.
- `-i/--input` (default `ip_prefixes.txt`), `-o/--output` (default
  `whois_results.txt`), `--no-progress`. Shows a per-prefix progress line and a
  summary (looked up / invalid / failed); each prefix is queried once.

```sh
./whois-checker.py -i prefixes.txt -o report.txt
```

### `spamhaus-check.sh`
Checks a single IP or a whole CIDR against the Spamhaus **ZEN** DNSBL, decoding
each hit (SBL / XBL / PBL / CSS / DROP) and colorizing output (green=clean,
red=listed, yellow=error). Exits non-zero if anything is listed.

- **bash** + `dig`; `prips` needed for CIDR expansion.
- Shows a live `[N/total]` progress counter with elapsed time; `-o/--output FILE`
  saves a plain-text report; `-l/--listed-only` hides clean IPs.
- **Caveat:** Spamhaus blocks queries via public resolvers (Google/Level3/etc.),
  which return `127.255.255.x` error codes. Query your own/ISP resolver or a
  Spamhaus DQS zone for real results — pass it as the optional 2nd argument.

```sh
./spamhaus-check.sh 192.0.2.0/24
./spamhaus-check.sh 198.51.100.5 9.9.9.9        # via a specific resolver
./spamhaus-check.sh -l -o report.txt 192.0.2.0/24
```

### `domain-info.sh`
Summarizes a domain's DNS footprint — A, MX, and NS records plus the owning org
(via whois) for the A host and primary MX host.

- **bash** + `dig`, `whois`. Linux + macOS portable.
- Reports each lookup stage on stderr and warns (instead of printing blank lines)
  when a record is missing; the clean report goes to stdout.

```sh
./domain-info.sh example.com
```

### `network-imix-perf-test.sh`
Automated network performance tester built on `iperf3`. Each batch launches
`--processes` iperf3 clients **concurrently** using IMIX packet-size patterns
(64/576/1500 B) and configurable bandwidth (50–450 Mbps), then prints a per-batch
summary (ok/failed, TCP/UDP split). Loops forever unless bounded.

- **bash** + `iperf3`. Linux + macOS portable (portable random + ping). Config is
  read from `~/.network_test.conf`; logs land in `~/network_tests/logs/`.
- Key flags: `-s/--server IP` (required), `-c/--count N` batches, `--max-time SEC`,
  `-d/--duration SEC` per test, `-p/--processes N`. CLI overrides the config file.

```sh
./network-imix-perf-test.sh -s 10.0.0.5 --count 3 --processes 5
./network-imix-perf-test.sh -s 10.0.0.5 --max-time 300 --duration 30
```
