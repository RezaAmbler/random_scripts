#!/usr/bin/env python3
"""
whois-checker.py — look up and parse WHOIS records for a list of IP prefixes.

Reads prefixes (one per line) from an input file, runs `whois` on each, parses
the interesting fields into an aligned table on stdout, and writes the full raw
WHOIS text to an output file. Reports progress per prefix and a summary at the
end (looked up / invalid / failed).

USAGE:
    ./whois-checker.py                              # ip_prefixes.txt -> whois_results.txt
    ./whois-checker.py -i prefixes.txt -o out.txt
    ./whois-checker.py -i prefixes.txt --no-progress   # quiet, good for logs

DEPENDENCIES: the system `whois` command. Python 3, stdlib only.
"""
import argparse
import ipaddress
import os
import re
import subprocess
import sys
import time

# Fields to pull out of WHOIS output. Matched case-insensitively (see parse).
PATTERNS = {
    'OrgName': r'OrgName:\s*(.*)',
    'OrgId': r'OrgId:\s*(.*)',
    'NetName': r'NetName:\s*(.*)',
    'NetHandle': r'NetHandle:\s*(.*)',
    'CIDR': r'CIDR:\s*(.*)',
    'Description': r'descr:\s*(.*)',
}

HEADERS = ['Prefix', 'OrgName', 'OrgId', 'NetName', 'NetHandle', 'CIDR', 'Description']


# ---------------------------------------------------------------------------
# Color / progress helpers (TTY- and NO_COLOR-aware)
# ---------------------------------------------------------------------------

def _supports_color():
    return sys.stderr.isatty() and not os.environ.get('NO_COLOR')


_COLOR = _supports_color()
BOLD = '\033[1m' if _COLOR else ''
DIM = '\033[2m' if _COLOR else ''
RED = '\033[31m' if _COLOR else ''
GREEN = '\033[32m' if _COLOR else ''
YELLOW = '\033[33m' if _COLOR else ''
RESET = '\033[0m' if _COLOR else ''


class Progress(object):
    """In-place single-line progress on stderr, throttled to ~10/sec."""

    def __init__(self, enabled=True):
        self.enabled = enabled and sys.stderr.isatty()
        self.last_update = 0.0
        self.last_len = 0

    def update(self, message, force=False):
        if not self.enabled:
            return
        now = time.time()
        if not force and (now - self.last_update) < 0.1:
            return
        self.last_update = now
        padded = message.ljust(self.last_len)
        self.last_len = len(message)
        sys.stderr.write('\r' + padded)
        sys.stderr.flush()

    def clear(self):
        if not self.enabled:
            return
        sys.stderr.write('\r' + ' ' * self.last_len + '\r')
        sys.stderr.flush()
        self.last_len = 0


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def whois_lookup(ip_prefix):
    """Return (ok, text). ok=False means the lookup itself failed."""
    try:
        result = subprocess.run(
            ['whois', ip_prefix],
            capture_output=True, text=True, timeout=10,
        )
        return True, result.stdout
    except subprocess.TimeoutExpired:
        return False, "Timeout occurred for {0}".format(ip_prefix)
    except OSError as e:
        return False, "Error occurred for {0}: {1}".format(ip_prefix, e)


def parse_whois_output(output):
    results = {}
    for key, pattern in PATTERNS.items():
        match = re.search(pattern, output, re.IGNORECASE)
        results[key] = match.group(1).strip() if match else 'N/A'
    return results


def print_table_row(data, widths):
    row = '| '
    for item, width in zip(data, widths):
        row += '{0:<{1}} | '.format(item, width)
    print(row)


def print_separator(widths):
    print('+' + '+'.join('-' * (w + 2) for w in widths) + '+')


def parse_args():
    parser = argparse.ArgumentParser(
        description='Look up and parse WHOIS records for a list of IP prefixes.')
    parser.add_argument('-i', '--input', default='ip_prefixes.txt',
                        help='input file, one prefix per line (default: ip_prefixes.txt)')
    parser.add_argument('-o', '--output', default='whois_results.txt',
                        help='file for full raw WHOIS text (default: whois_results.txt)')
    parser.add_argument('--no-progress', action='store_true',
                        help='disable the in-place progress line')
    return parser.parse_args()


def main():
    args = parse_args()
    progress = Progress(enabled=not args.no_progress)

    if not os.path.exists(args.input):
        print("{0}Error:{1} input file not found: {2}".format(RED, RESET, args.input),
              file=sys.stderr)
        sys.exit(2)

    try:
        with open(args.input, 'r') as f:
            prefixes = [line.strip() for line in f if line.strip()]
    except OSError as e:
        print("{0}Error reading {1}:{2} {3}".format(RED, args.input, RESET, e),
              file=sys.stderr)
        sys.exit(2)

    if not prefixes:
        print("No prefixes found in {0}.".format(args.input))
        sys.exit(0)

    total = len(prefixes)
    print("{0}Looking up {1} prefix(es) from {2}{3}".format(
        BOLD, total, args.input, RESET), file=sys.stderr)

    widths = [len(h) for h in HEADERS]
    results = []          # list of (parsed_dict, raw_text) for valid prefixes
    invalid = 0
    failed = 0
    start = time.time()

    for idx, prefix in enumerate(prefixes, 1):
        try:
            ipaddress.ip_network(prefix, strict=False)
        except ValueError:
            progress.clear()
            print("{0}Invalid IP prefix:{1} {2}".format(YELLOW, RESET, prefix),
                  file=sys.stderr)
            invalid += 1
            continue

        progress.update("[whois] {0}/{1} | {2}".format(idx, total, prefix))

        ok, raw = whois_lookup(prefix)          # single lookup (was queried twice)
        if not ok:
            progress.clear()
            print("{0}Lookup failed:{1} {2} ({3})".format(RED, RESET, prefix, raw),
                  file=sys.stderr)
            failed += 1
            continue

        parsed = parse_whois_output(raw)
        parsed['Prefix'] = prefix
        results.append((parsed, raw))

        for i, key in enumerate(HEADERS):
            widths[i] = max(widths[i], len(str(parsed.get(key, 'N/A'))))

    progress.clear()
    elapsed = time.time() - start

    # Table to stdout.
    print_separator(widths)
    print_table_row(HEADERS, widths)
    print_separator(widths)

    try:
        with open(args.output, 'w') as f:
            for parsed, raw in results:
                print_table_row([parsed.get(key, 'N/A') for key in HEADERS], widths)
                f.write("WHOIS lookup for {0}:\n".format(parsed['Prefix']))
                f.write(raw)                      # reuse text from the single lookup
                f.write("\n" + "=" * 50 + "\n\n")
    except OSError as e:
        print_separator(widths)
        print("{0}Error writing {1}:{2} {3}".format(RED, args.output, RESET, e),
              file=sys.stderr)
        sys.exit(1)

    print_separator(widths)

    # Summary.
    print("\n{0}Summary:{1}".format(BOLD, RESET), file=sys.stderr)
    print("  {0}Looked up:{1} {2}".format(GREEN, RESET, len(results)), file=sys.stderr)
    if invalid:
        print("  {0}Invalid:  {1} {2}".format(YELLOW, RESET, invalid), file=sys.stderr)
    if failed:
        print("  {0}Failed:   {1} {2}".format(RED, RESET, failed), file=sys.stderr)
    print("  Elapsed:   {0:.1f}s".format(elapsed), file=sys.stderr)
    print("\nFull WHOIS results saved to {0}".format(args.output), file=sys.stderr)


if __name__ == '__main__':
    main()
