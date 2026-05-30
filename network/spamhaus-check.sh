#!/bin/bash
#
# spamhaus-check.sh — Check a range of IPs against the Spamhaus ZEN DNSBL.
#
# For each IP it reverses the octets and looks up
# <4>.<3>.<2>.<1>.zen.spamhaus.org. A returned 127.0.0.x address means the IP
# is listed; the script decodes which Spamhaus list it hit. Clean IPs are shown
# in green, listings in red, and Spamhaus error codes in yellow.
#
# USAGE:
#   ./spamhaus-check.sh <CIDR-or-IP> [resolver]
#   ./spamhaus-check.sh 192.0.2.0/24
#   ./spamhaus-check.sh 198.51.100.5
#   ./spamhaus-check.sh 192.0.2.0/24 9.9.9.9     # query a specific resolver
#
# IMPORTANT — public resolvers do NOT work:
#   Spamhaus blocks DNSBL queries that arrive via large public/open resolvers
#   (Google 8.8.8.8 / 8.8.4.4, Level3 4.2.2.x, etc.). Those return a
#   127.255.255.x ERROR code, not real listing data — so the original version
#   of this script (which round-robined Google/Level3) could not give correct
#   answers. For accurate results query your own ISP/recursive resolver, or a
#   Spamhaus Data Query Service (DQS) zone. With no [resolver] argument this
#   uses your system default resolver.
#
# DEPENDENCIES: dig (dnsutils/bind-utils); prips for CIDR expansion.

set -u

TARGET="${1:-}"
RESOLVER="${2:-}"

if [[ -z "$TARGET" ]]; then
    echo "Usage: $0 <CIDR-or-IP> [resolver]" >&2
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

# Decode a Spamhaus ZEN return code into (colour, label).
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

# Expand the target into a list of IPs.
if [[ "$TARGET" == */* ]]; then
    if ! command -v prips >/dev/null 2>&1; then
        echo "Error: 'prips' is required to expand a CIDR ($TARGET)." >&2
        echo "Install it, or pass a single IP instead." >&2
        exit 1
    fi
    mapfile -t IPS < <(prips "$TARGET")
else
    IPS=("$TARGET")
fi

echo "${BOLD}Checking ${#IPS[@]} IP(s) against zen.spamhaus.org${RESET}"
[[ -n "$RESOLVER" ]] && echo "Resolver: $RESOLVER"
echo

listed_count=0
for ip in "${IPS[@]}"; do
    IFS=. read -r o1 o2 o3 o4 <<< "$ip"
    query="${o4}.${o3}.${o2}.${o1}.zen.spamhaus.org"

    answers="$(dig +short ${RESOLVER:+@"$RESOLVER"} "$query" A 2>/dev/null)"

    if [[ -z "$answers" ]]; then
        printf "  %-15s %sclean%s\n" "$ip" "$GREEN" "$RESET"
        continue
    fi

    first=1
    while read -r code; do
        [[ -z "$code" ]] && continue
        [[ "$code" == 127.255.* ]] || listed_count=$((listed_count + 1))
        if [[ $first -eq 1 ]]; then
            printf "  %-15s %s\n" "$ip" "$(decode_code "$code")"
            first=0
        else
            printf "  %-15s %s\n" "" "$(decode_code "$code")"
        fi
    done <<< "$answers"
done

echo
echo "${BOLD}Done.${RESET} ${listed_count} listing(s) across ${#IPS[@]} IP(s)."
# Exit 1 if anything was actually listed (handy as a monitoring signal).
[[ $listed_count -gt 0 ]] && exit 1
exit 0
