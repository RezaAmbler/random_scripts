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

### `network-imix-perf-test.sh`
Automated network performance tester built on `iperf3`. Generates concurrent
TCP/UDP traffic using IMIX packet-size patterns (64/576/1500 B), with configurable
bandwidth (50–450 Mbps), multiple processes, and timestamped logging.

- **bash** + `iperf3`. Config is read from `~/.network_test.conf`; logs land in
  `~/network_tests/logs/`. Set `SERVER_IP` before running.

```sh
./network-imix-perf-test.sh
```
