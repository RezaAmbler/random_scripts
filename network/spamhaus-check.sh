#!/bin/bash
#
# spamhaus-check.sh — Check a range of IPs against the Spamhaus ZEN DNSBL.
#
# For each IP it reverses the octets and looks up
# <4>.<3>.<2>.<1>.zen.spamhaus.org. A returned 127.0.0.x address means the IP
# is listed; the script decodes which Spamhaus list it hit. Clean IPs are shown
# in green, listings in red, and Spamhaus error codes in yellow. A live
# [N/total] counter (with elapsed time) is shown while it works.
#
# USAGE:
#   ./spamhaus-check.sh [options] <CIDR-or-IP> [resolver]
#   ./spamhaus-check.sh 192.0.2.0/24
#   ./spamhaus-check.sh 198.51.100.5
#   ./spamhaus-check.sh 192.0.2.0/24 9.9.9.9          # query a specific resolver
#   ./spamhaus-check.sh -l 192.0.2.0/24               # only print listings
#   ./spamhaus-check.sh -o report.txt 192.0.2.0/24    # also save a plain report
#
# OPTIONS:
#   -o, --output FILE    write a plain-text (no color) report to FILE
#   -l, --listed-only    only print IPs that are listed/errored (hide "clean")
#   -h, --help           show this help
#
# IMPORTANT — public resolvers do NOT work:
#   Spamhaus blocks DNSBL queries that arrive via large public/open resolvers
#   (Google 8.8.8.8 / 8.8.4.4, Level3 4.2.2.x, etc.). Those return a
#   127.255.255.x ERROR code, not real listing data. For accurate results query
#   your own ISP/recursive resolver, or a Spamhaus Data Query Service (DQS)
#   zone. With no [resolver] argument this uses your system default resolver.
#
# DEPENDENCIES: dig (dnsutils/bind-utils); prips for CIDR expansion.

set -u
set -o pipefail

OUTPUT=""
LISTED_ONLY=0
POSITIONAL=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        -o|--output)      OUTPUT="${2:-}"; shift 2 ;;
        -l|--listed-only) LISTED_ONLY=1; shift ;;
        -h|--help)        sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        -*)               echo "Unknown option: $1" >&2; exit 1 ;;
        *)                POSITIONAL+=("$1"); shift ;;
    esac
done

TARGET="${POSITIONAL[0]:-}"
RESOLVER="${POSITIONAL[1]:-}"

if [[ -z "$TARGET" ]]; then
    echo "Usage: $0 [options] <CIDR-or-IP> [resolver]" >&2
    exit 1
fi

if ! command -v dig >/dev/null 2>&1; then
    echo "Error: 'dig' is required (install dnsutils / bind-utils)." >&2
    exit 1
fi

# Colors — only when stdout is a terminal and NO_COLOR is unset.
if [[ -t 1 && -z "${NO_COLOR:-}" ]] && command -v tput >/dev/null 2>&1; then
    RED="$(tput setaf 1)"; GREEN="$(tput setaf 2)"
    YELLOW="$(tput setaf 3)"; BOLD="$(tput bold)"; RESET="$(tput sgr0)"
else
    RED=""; GREEN=""; YELLOW=""; BOLD=""; RESET=""
fi

# Truthy if stderr is a terminal (so the live counter only shows interactively).
PROGRESS=0
[[ -t 2 ]] && PROGRESS=1

# Decode a Spamhaus ZEN return code into a colored, human label.
decode_code() {
    case "$1" in
        127.0.0.2)            echo "${RED}SBL — Spamhaus Blocklist${RESET}" ;;
        127.0.0.3)            echo "${RED}SBL CSS — snowshoe/listed sender${RESET}" ;;
        127.0.0.4|127.0.0.5|127.0.0.6|127.0.0.7)
                              echo "${RED}XBL — exploited/infected (CBL)${RESET}" ;;
        127.0.0.9)            echo "${RED}SBL DROP/EDROP — hijacked netblock${RESET}" ;;
        127.0.0.10|127.0.0.11)
                              echo "${RED}PBL — end-user/dynamic IP space${RESET}" ;;
        127.255.255.252)      echo "${YELLOW}ERROR — typing error or public-DNS overuse${RESET}" ;;
        127.255.255.254)      echo "${YELLOW}ERROR — query came via a public/open resolver (not allowed)${RESET}" ;;
        127.255.255.255)      echo "${YELLOW}ERROR — excessive queries, you are rate-limited${RESET}" ;;
        *)                    echo "${YELLOW}listed (unknown code $1)${RESET}" ;;
    esac
}

# Plain (uncolored) label for the --output report.
decode_plain() {
    # Strip color escapes from decode_code's output.
    decode_code "$1" | sed $'s/\033\\[[0-9;]*m//g'
}

# Expand the target into a list of IPs.
if [[ "$TARGET" == */* ]]; then
    if ! command -v prips >/dev/null 2>&1; then
        echo "Error: 'prips' is required to expand a CIDR ($TARGET)." >&2
        echo "Install it, or pass a single IP instead." >&2
        exit 1
    fi
    IPS=()
    while IFS= read -r line; do IPS+=("$line"); done < <(prips "$TARGET")
else
    IPS=("$TARGET")
fi

total=${#IPS[@]}

# Optional report file (truncate up front so a failed run leaves a clean file).
if [[ -n "$OUTPUT" ]]; then
    : > "$OUTPUT" || { echo "Error: cannot write to $OUTPUT" >&2; exit 1; }
    {
        echo "Spamhaus ZEN check — $total IP(s)${RESOLVER:+ via resolver $RESOLVER}"
        echo
    } >> "$OUTPUT"
fi

echo "${BOLD}Checking ${total} IP(s) against zen.spamhaus.org${RESET}"
[[ -n "$RESOLVER" ]] && echo "Resolver: $RESOLVER"
echo

SECONDS=0
listed_count=0
n=0

# Emit one result line to stdout (colored) and, if requested, the report file.
emit() {
    local ip="$1" label_color="$2" label_plain="$3"
    printf "  %-15s %s\n" "$ip" "$label_color"
    [[ -n "$OUTPUT" ]] && printf "  %-15s %s\n" "$ip" "$label_plain" >> "$OUTPUT"
}

for ip in "${IPS[@]}"; do
    n=$((n + 1))

    # Live progress counter on stderr (overwritten in place).
    if [[ $PROGRESS -eq 1 ]]; then
        printf "\r${BOLD}[%d/%d]${RESET} %ss elapsed — checking %s\033[K" \
            "$n" "$total" "$SECONDS" "$ip" >&2
    fi

    IFS=. read -r o1 o2 o3 o4 <<< "$ip"
    query="${o4}.${o3}.${o2}.${o1}.zen.spamhaus.org"

    answers="$(dig +short ${RESOLVER:+@"$RESOLVER"} "$query" A 2>/dev/null)"

    if [[ -z "$answers" ]]; then
        if [[ $LISTED_ONLY -eq 0 ]]; then
            [[ $PROGRESS -eq 1 ]] && printf "\r\033[K" >&2
            emit "$ip" "${GREEN}clean${RESET}" "clean"
        fi
        continue
    fi

    [[ $PROGRESS -eq 1 ]] && printf "\r\033[K" >&2
    while read -r code; do
        [[ -z "$code" ]] && continue
        [[ "$code" == 127.255.* ]] || listed_count=$((listed_count + 1))
        emit "$ip" "$(decode_code "$code")" "$(decode_plain "$code")"
    done <<< "$answers"
done

# Clear the progress line.
[[ $PROGRESS -eq 1 ]] && printf "\r\033[K" >&2

summary="Done in ${SECONDS}s. ${listed_count} listing(s) across ${total} IP(s)."
echo
echo "${BOLD}${summary}${RESET}"
[[ -n "$OUTPUT" ]] && { echo; echo "$summary"; } >> "$OUTPUT"
[[ -n "$OUTPUT" ]] && echo "Report saved to: $OUTPUT"

# Exit 1 if anything was actually listed (handy as a monitoring signal).
[[ $listed_count -gt 0 ]] && exit 1
exit 0
