#!/bin/bash
#
# domain-info.sh — Summarize a domain's DNS footprint: A, MX and NS records,
# plus the organization (via whois) that owns the A host and the primary MX
# host. If the A and MX hosts belong to the same org the output is condensed.
#
# USAGE: ./domain-info.sh example.com
#
# DEPENDENCIES: dig, whois.

DOMAIN="$1"
if [[ -z "$DOMAIN" ]]; then
    echo "Usage: $0 <domain>" >&2
    exit 1
fi

ARECORD=$(dig a +short "$DOMAIN")
MXRECORD=$(dig mx +short "$DOMAIN")
NSRECORD=$(dig ns +short "$DOMAIN")

# Resolve the lowest-preference (primary) MX hostname to its A record(s).
# (Original used Solaris 'nawk'; plain 'awk' is portable to Linux/macOS.)
MXARECORD=$(dig mx +short "$DOMAIN" | sort -n | awk '{print $2; exit}' | dig +short -f -)

# Extract the owning organization from whois output. Different RIRs use
# different field names, so match the common ones and trim whitespace.
org_of() {
    [ -z "$1" ] && return
    whois "$1" 2>/dev/null | awk -F: '
        /^(Organization|OrgName|org-name|owner|netname):/ {
            sub(/^[ \t]+/, "", $2); print $2; exit
        }'
}

ANETNAME=$(org_of "$(echo "$ARECORD" | awk '{print $1}')")
MXNETNAME=$(org_of "$MXARECORD")

if [ "$ANETNAME" = "$MXNETNAME" ]; then
    printf "A:   Host: %s\n%s\n\nMX:\n%s\n\nNS:\n%s\n" \
        "$ANETNAME" "$ARECORD" "$MXRECORD" "$NSRECORD"
else
    printf "A:   Host: %s\n%s\n\nMX:  Host: %s\n%s\n\nNS:\n%s\n" \
        "$ANETNAME" "$ARECORD" "$MXNETNAME" "$MXRECORD" "$NSRECORD"
fi
