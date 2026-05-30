#!/bin/bash
# network-imix-perf-test.sh — Automated network testing using iperf3 with IMIX
# patterns: concurrent TCP/UDP traffic, configurable bandwidth (50-450 Mbps),
# multiple packet sizes, and comprehensive logging.
#
# Each "batch" launches NUM_PROCESSES iperf3 clients CONCURRENTLY (real parallel
# load), waits for them, then prints a per-batch summary. By default it loops
# forever; use --count or --max-time to bound the run. Live progress shows how
# many tests in the current batch have finished.
#
# USAGE:
#   ./network-imix-perf-test.sh -s 10.0.0.5
#   ./network-imix-perf-test.sh -s 10.0.0.5 --count 3 --processes 5
#   ./network-imix-perf-test.sh -s 10.0.0.5 --max-time 300 --duration 30
#
# OPTIONS (override the config file, which overrides built-in defaults):
#   -s, --server IP        target iperf3 server (required if not in config)
#   -c, --count N          run N batches then stop (0 or unset = infinite)
#   -d, --duration SEC     seconds per individual iperf3 test (default 60)
#   -p, --processes N      concurrent tests per batch (default 10)
#       --max-time SEC     stop after this many seconds of wall-clock total
#       --min-bw MBPS      min UDP bandwidth (default 50)
#       --max-bw MBPS      max UDP bandwidth (default 450)
#       --tcp-pct PCT      percent of tests that use TCP (default 30)
#   -h, --help             show this help
#
# DEPENDENCIES: iperf3. Portable to Linux and macOS.

set -uo pipefail
# NOTE: 'set -e' is intentionally NOT used. This is a resilient long-running load
# generator — a single failed iperf3 run should be counted, not abort the suite.

###################################################################################
# Configuration (defaults — overridden by config file, then by CLI args)
###################################################################################

CONFIG_FILE="${HOME}/.network_test.conf"
LOG_DIR="${HOME}/network_tests/logs"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="${LOG_DIR}/network_test_${TIMESTAMP}.log"

SERVER_IP="<server_ip>"     # Target iperf3 server IP address
DURATION=60                 # Duration of each test in seconds
MIN_BW=50                   # Minimum bandwidth for UDP tests (Mbps)
MAX_BW=450                  # Maximum bandwidth for UDP tests (Mbps)
IMIX_SIZES=(64 576 1500)    # IMIX packet sizes in bytes (voice / small / large)
IMIX_WEIGHTS=(50 30 20)     # Weight distribution for IMIX sizes
TCP_PERCENTAGE=30           # Percentage of tests that should use TCP
NUM_PROCESSES=10            # Number of concurrent iperf3 processes per batch
COUNT=0                     # Number of batches (0 = infinite)
MAX_TIME=0                  # Wall-clock budget in seconds (0 = unlimited)

# Global counters
declare -i TOTAL_TESTS=0
declare -i FAILED_TESTS=0
declare -i BATCH=0
START_TIME=$(date +%s)
RUNNING=1

# CLI overrides (empty = not supplied; applied after the config file loads).
OPT_SERVER=""; OPT_COUNT=""; OPT_DURATION=""; OPT_PROCESSES=""
OPT_MAXTIME=""; OPT_MINBW=""; OPT_MAXBW=""; OPT_TCPPCT=""

###################################################################################
# Colors (console only; the log file stays plain)
###################################################################################

if [[ -t 2 && -z "${NO_COLOR:-}" ]] && command -v tput >/dev/null 2>&1; then
    C_BOLD="$(tput bold)"; C_CYAN="$(tput setaf 6)"; C_GREEN="$(tput setaf 2)"
    C_YELLOW="$(tput setaf 3)"; C_RED="$(tput setaf 1)"; C_RESET="$(tput sgr0)"
else
    C_BOLD=""; C_CYAN=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_RESET=""
fi

usage() { sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; }

###################################################################################
# Argument parsing
###################################################################################

while [[ $# -gt 0 ]]; do
    case "$1" in
        -s|--server)    OPT_SERVER="${2:-}"; shift 2 ;;
        -c|--count)     OPT_COUNT="${2:-}"; shift 2 ;;
        -d|--duration)  OPT_DURATION="${2:-}"; shift 2 ;;
        -p|--processes) OPT_PROCESSES="${2:-}"; shift 2 ;;
        --max-time)     OPT_MAXTIME="${2:-}"; shift 2 ;;
        --min-bw)       OPT_MINBW="${2:-}"; shift 2 ;;
        --max-bw)       OPT_MAXBW="${2:-}"; shift 2 ;;
        --tcp-pct)      OPT_TCPPCT="${2:-}"; shift 2 ;;
        -h|--help)      usage; exit 0 ;;
        *)              echo "Unknown option: $1" >&2; usage; exit 1 ;;
    esac
done

###################################################################################
# Logging / progress
###################################################################################

# Plain, timestamped line to both the log file and the console.
log_message() {
    local timestamp; timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[${timestamp}] $1" | tee -a "${LOG_FILE}"
}

# Colored, console-only banner (also logged plain so the file keeps a record).
banner() {
    echo "${C_BOLD}${C_CYAN}$1${C_RESET}" >&2
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "${LOG_FILE}"
}

# In-place progress line on stderr (only when interactive).
progress() {
    [[ -t 2 ]] || return 0
    printf "\r%s\033[K" "$1" >&2
}
progress_clear() { [[ -t 2 ]] && printf "\r\033[K" >&2; }

###################################################################################
# Config / validation
###################################################################################

load_config() {
    if [[ -f "${CONFIG_FILE}" ]]; then
        # shellcheck disable=SC1090
        source "${CONFIG_FILE}"
        log_message "Loaded configuration from ${CONFIG_FILE}"
    else
        log_message "No configuration file found, using defaults"
    fi
}

apply_overrides() {
    [[ -n "$OPT_SERVER" ]]    && SERVER_IP="$OPT_SERVER"
    [[ -n "$OPT_COUNT" ]]     && COUNT="$OPT_COUNT"
    [[ -n "$OPT_DURATION" ]]  && DURATION="$OPT_DURATION"
    [[ -n "$OPT_PROCESSES" ]] && NUM_PROCESSES="$OPT_PROCESSES"
    [[ -n "$OPT_MAXTIME" ]]   && MAX_TIME="$OPT_MAXTIME"
    [[ -n "$OPT_MINBW" ]]     && MIN_BW="$OPT_MINBW"
    [[ -n "$OPT_MAXBW" ]]     && MAX_BW="$OPT_MAXBW"
    [[ -n "$OPT_TCPPCT" ]]    && TCP_PERCENTAGE="$OPT_TCPPCT"
}

validate_config() {
    if [[ ! "$SERVER_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        log_message "ERROR: Invalid/!unset server IP: '${SERVER_IP}' (use -s/--server)"
        exit 1
    fi
    if (( TCP_PERCENTAGE < 0 || TCP_PERCENTAGE > 100 )); then
        log_message "ERROR: Invalid TCP_PERCENTAGE: ${TCP_PERCENTAGE}"
        exit 1
    fi
    if (( MAX_BW < MIN_BW )); then
        log_message "ERROR: MAX_BW (${MAX_BW}) < MIN_BW (${MIN_BW})"
        exit 1
    fi
}

check_dependencies() {
    if ! command -v iperf3 >/dev/null 2>&1; then
        log_message "ERROR: iperf3 is not installed"
        exit 1
    fi
}

setup_directories() {
    mkdir -p "${LOG_DIR}"
    chmod 755 "${LOG_DIR}"
}

###################################################################################
# Test selection helpers
###################################################################################

# Weighted-random IMIX packet size.
get_imix_packet_size() {
    local total_weight=0 weight
    for weight in "${IMIX_WEIGHTS[@]}"; do
        total_weight=$((total_weight + weight))
    done
    local rand=$((RANDOM % total_weight))
    local cumulative=0 i
    for i in "${!IMIX_WEIGHTS[@]}"; do
        cumulative=$((cumulative + IMIX_WEIGHTS[i]))
        if (( rand < cumulative )); then
            echo "${IMIX_SIZES[i]}"
            return
        fi
    done
    echo "${IMIX_SIZES[-1]}"
}

# Random bandwidth in [MIN_BW, MAX_BW]. Uses $RANDOM (portable) not GNU shuf.
random_bw() { echo $((MIN_BW + RANDOM % (MAX_BW - MIN_BW + 1))); }

choose_protocol() {
    if (( (RANDOM % 100) < TCP_PERCENTAGE )); then echo "TCP"; else echo "UDP"; fi
}

# Portable single ping: Linux uses -W (seconds), macOS/BSD uses -t (seconds).
ping_once() {
    case "$(uname -s)" in
        Darwin|*BSD) ping -c 1 -t 2 "$1" >/dev/null 2>&1 ;;
        *)           ping -c 1 -W 2 "$1" >/dev/null 2>&1 ;;
    esac
}

check_server() {
    if ! ping_once "${SERVER_IP}"; then
        log_message "ERROR: Cannot reach server ${SERVER_IP}"
        return 1
    fi
    return 0
}

###################################################################################
# Test execution
###################################################################################

# Run one iperf3 test (in the background) and write its result to $2.
# Result line format: "OK|FAIL PROTO SIZE BW"
run_one() {
    local id="$1" statusfile="$2"
    local protocol; protocol=$(choose_protocol)
    local test_id="${TIMESTAMP}_b${BATCH}_${id}"
    local size="-" bw="-" rc=0

    if [[ "$protocol" == "UDP" ]]; then
        bw=$(random_bw); size=$(get_imix_packet_size)
        iperf3 -c "${SERVER_IP}" -u -b "${bw}M" -l "${size}" -t "${DURATION}" \
            --logfile "${LOG_DIR}/iperf_${test_id}.log" >/dev/null 2>&1 || rc=1
    else
        iperf3 -c "${SERVER_IP}" -t "${DURATION}" \
            --logfile "${LOG_DIR}/iperf_${test_id}.log" >/dev/null 2>&1 || rc=1
    fi

    if (( rc == 0 )); then echo "OK ${protocol} ${size} ${bw}" > "$statusfile"
    else echo "FAIL ${protocol} ${size} ${bw}" > "$statusfile"; fi
}

# Launch a concurrent batch, show live progress, then tally + summarize.
run_batch() {
    BATCH=$((BATCH + 1))
    local dir; dir=$(mktemp -d "${TMPDIR:-/tmp}/imix.XXXXXX")
    local i
    for (( i = 1; i <= NUM_PROCESSES; i++ )); do
        run_one "$i" "${dir}/${i}" &
    done

    # Live progress: count completed (each finished test writes its status file).
    local done=0 batch_start; batch_start=$(date +%s)
    while (( done < NUM_PROCESSES )); do
        done=$(find "$dir" -type f 2>/dev/null | wc -l | tr -d ' ')
        progress "${C_BOLD}[batch ${BATCH}]${C_RESET} ${done}/${NUM_PROCESSES} tests done — $(( $(date +%s) - batch_start ))s"
        (( done < NUM_PROCESSES )) && sleep 1
    done
    wait
    progress_clear

    # Tally results.
    local ok=0 fail=0 tcp=0 udp=0 result proto size bw f
    for f in "$dir"/*; do
        read -r result proto size bw < "$f"
        if [[ "$result" == "OK" ]]; then ok=$((ok+1)); else fail=$((fail+1)); fi
        if [[ "$proto" == "TCP" ]]; then tcp=$((tcp+1)); else udp=$((udp+1)); fi
    done
    rm -rf "$dir"

    TOTAL_TESTS=$(( TOTAL_TESTS + NUM_PROCESSES ))
    FAILED_TESTS=$(( FAILED_TESTS + fail ))

    local color="$C_GREEN"; (( fail > 0 )) && color="$C_YELLOW"
    banner "Batch ${BATCH}: ${ok} ok, ${fail} failed (${tcp} TCP / ${udp} UDP), $(( $(date +%s) - batch_start ))s"
    echo "${color}  └─ cumulative: ${TOTAL_TESTS} tests, ${FAILED_TESTS} failed${C_RESET}" >&2
}

###################################################################################
# Cleanup / summary
###################################################################################

cleanup() {
    local exit_code=$?
    progress_clear
    log_message "Cleaning up..."
    pkill -f iperf3 2>/dev/null || true

    local duration=$(( $(date +%s) - START_TIME ))
    local rate=0
    (( TOTAL_TESTS > 0 )) && rate=$(( (TOTAL_TESTS - FAILED_TESTS) * 100 / TOTAL_TESTS ))

    log_message "Test Summary:"
    log_message "  Total Duration: ${duration}s"
    log_message "  Batches:        ${BATCH}"
    log_message "  Total Tests:    ${TOTAL_TESTS}"
    log_message "  Failed Tests:   ${FAILED_TESTS}"
    log_message "  Success Rate:   ${rate}%"
    exit "${exit_code}"
}

stop_running() { RUNNING=0; banner "Stop requested — finishing current batch..."; }

###################################################################################
# Main
###################################################################################

main() {
    setup_directories
    trap cleanup EXIT
    trap stop_running INT TERM

    check_dependencies
    load_config
    apply_overrides
    validate_config

    banner "Starting network test suite"
    log_message "Server IP:  ${SERVER_IP}"
    log_message "Duration:   ${DURATION}s per test"
    log_message "Processes:  ${NUM_PROCESSES} concurrent"
    log_message "Batches:    $([[ $COUNT -eq 0 ]] && echo 'infinite' || echo "$COUNT")"
    [[ $MAX_TIME -gt 0 ]] && log_message "Max time:   ${MAX_TIME}s"

    while (( RUNNING )); do
        if ! check_server; then
            log_message "Server unreachable; retrying in 60s"
            sleep 60
            continue
        fi

        run_batch

        # Stop conditions.
        (( COUNT > 0 && BATCH >= COUNT )) && break
        if (( MAX_TIME > 0 )) && (( $(date +%s) - START_TIME >= MAX_TIME )); then
            log_message "Reached max-time budget (${MAX_TIME}s)"
            break
        fi
    done
}

main
