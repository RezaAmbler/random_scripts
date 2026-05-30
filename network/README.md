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

```sh
python3 router-views-lookup-mass.py --prefix 8.8.8.0/24 --config route_servers.yaml
python3 router-views-lookup-mass.py -f prefixes.txt --save results.txt
```

### `whois-checker.py`
Looks up WHOIS records for IP prefixes via the system `whois` command and parses
out the interesting fields (OrgName, NetName, CIDR, etc.).

- **Python 3**, stdlib only. Requires the `whois` CLI to be installed.

### `spamhaus-check.sh`
Checks a single IP or a whole CIDR against the Spamhaus **ZEN** DNSBL, decoding
each hit (SBL / XBL / PBL / CSS / DROP) and colorizing output (green=clean,
red=listed, yellow=error). Exits non-zero if anything is listed.

- **bash** + `dig`; `prips` needed for CIDR expansion.
- **Caveat:** Spamhaus blocks queries via public resolvers (Google/Level3/etc.),
  which return `127.255.255.x` error codes. Query your own/ISP resolver or a
  Spamhaus DQS zone for real results — pass it as the optional 2nd argument.

```sh
./spamhaus-check.sh 192.0.2.0/24
./spamhaus-check.sh 198.51.100.5 9.9.9.9   # via a specific resolver
```

### `domain-info.sh`
Summarizes a domain's DNS footprint — A, MX, and NS records plus the owning org
(via whois) for the A host and primary MX host.

- **bash** + `dig`, `whois`.

```sh
./domain-info.sh example.com
```

### `network-imix-perf-test.sh`
Automated network performance tester built on `iperf3`. Generates concurrent
TCP/UDP traffic using IMIX packet-size patterns (64/576/1500 B), with configurable
bandwidth (50–450 Mbps), multiple processes, and timestamped logging.

- **bash** + `iperf3`. Config is read from `~/.network_test.conf`; logs land in
  `~/network_tests/logs/`. Set `SERVER_IP` before running.

```sh
./network-imix-perf-test.sh
```
