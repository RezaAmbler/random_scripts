#!/bin/bash
#
# domain-info.sh — Summarize a domain's DNS footprint: A, MX and NS records,
# plus the organization (via whois) that owns the A host and the primary MX
# host. If the A and MX hosts belong to the same org the output is condensed.
#
# Reports each stage as it runs and warns (instead of printing blank lines)
# when a lookup returns nothing.
#
# USAGE: ./domain-info.sh example.com
#
# DEPENDENCIES: dig, whois. Portable to Linux and macOS.

set -u

DOMAIN="${1:-}"

if [[ -z "$DOMAIN" ]]; then
    echo "Usage: $0 <domain>" >&2
    exit 1
fi

# Basic sanity check on the domain (letters, digits, dots, hyphens only).
if [[ ! "$DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]]; then
    echo "Error: '$DOMAIN' doesn't look like a domain name." >&2
    exit 1
fi

for tool in dig whois; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "Error: '$tool' is required but not installed." >&2
        exit 1
    fi
done

# Colors — only on a terminal and when NO_COLOR is unset.
if [[ -t 1 && -z "${NO_COLOR:-}" ]] && command -v tput >/dev/null 2>&1; then
    BOLD="$(tput bold)"; CYAN="$(tput setaf 6)"; YELLOW="$(tput setaf 3)"
    GREEN="$(tput setaf 2)"; RESET="$(tput sgr0)"
else
    BOLD=""; CYAN=""; YELLOW=""; GREEN=""; RESET=""
fi

# Progress/stage messages go to stderr so stdout stays the clean report.
stage() { echo "${CYAN}==>${RESET} $*" >&2; }
warn()  { echo "${YELLOW}warning:${RESET} $*" >&2; }

# Extract the owning organization from whois output. Different RIRs use
# different field names, so match the common ones and trim whitespace.
org_of() {
    [[ -z "$1" ]] && return
    whois "$1" 2>/dev/null | awk -F: '
        /^(Organization|OrgName|org-name|owner|netname):/ {
            sub(/^[ \t]+/, "", $2); print $2; exit
        }'
}

stage "Resolving A record for $DOMAIN"
ARECORD=$(dig a +short "$DOMAIN")
[[ -z "$ARECORD" ]] && warn "no A record found for $DOMAIN"

stage "Resolving MX records"
MXRECORD=$(dig mx +short "$DOMAIN")
[[ -z "$MXRECORD" ]] && warn "no MX records found for $DOMAIN"

stage "Resolving NS records"
NSRECORD=$(dig ns +short "$DOMAIN")
[[ -z "$NSRECORD" ]] && warn "no NS records found for $DOMAIN"

# Resolve the lowest-preference (primary) MX hostname to its A record(s).
stage "Resolving primary MX host address"
MXARECORD=$(printf '%s\n' "$MXRECORD" | sort -n | awk 'NF {print $2; exit}' | dig +short -f -)

stage "WHOIS org for A host"
ANETNAME=$(org_of "$(printf '%s\n' "$ARECORD" | awk 'NF {print $1; exit}')")
[[ -z "$ANETNAME" ]] && ANETNAME="(unknown)"

stage "WHOIS org for primary MX host"
MXNETNAME=$(org_of "$MXARECORD")
[[ -z "$MXNETNAME" ]] && MXNETNAME="(unknown)"

echo >&2   # blank line between progress and the report

# --- Report (stdout) -------------------------------------------------------
hdr() { printf "%s%s%s\n" "$BOLD$GREEN" "$1" "$RESET"; }

if [[ "$ANETNAME" == "$MXNETNAME" ]]; then
    hdr "A:   Host: $ANETNAME"
    printf "%s\n\n" "${ARECORD:-(none)}"
    hdr "MX:"
    printf "%s\n\n" "${MXRECORD:-(none)}"
    hdr "NS:"
    printf "%s\n" "${NSRECORD:-(none)}"
else
    hdr "A:   Host: $ANETNAME"
    printf "%s\n\n" "${ARECORD:-(none)}"
    hdr "MX:  Host: $MXNETNAME"
    printf "%s\n\n" "${MXRECORD:-(none)}"
    hdr "NS:"
    printf "%s\n" "${NSRECORD:-(none)}"
fi
