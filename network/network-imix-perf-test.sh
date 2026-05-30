#!/bin/bash
# network-imix-perf-test.sh — Automated network testing using iperf3 with IMIX
# patterns: concurrent TCP/UDP traffic, configurable bandwidth (50-450 Mbps),
# multiple packet sizes, and comprehensive logging.

###################################################################################
# Network Testing Script
# 
# This script performs comprehensive network testing using iperf3 with both TCP and UDP
# protocols. It supports IMIX packet sizes and various bandwidth configurations.
###################################################################################

# Configuration file and logging setup
# These variables define the locations for configuration and log files
# Using ${HOME} ensures the script works for any user
CONFIG_FILE="${HOME}/.network_test.conf"    # External configuration file location
LOG_DIR="${HOME}/network_tests/logs"        # Directory for storing all log files
TIMESTAMP=$(date +%Y%m%d_%H%M%S)           # Unique timestamp for this test run
LOG_FILE="${LOG_DIR}/network_test_${TIMESTAMP}.log"  # Main log file for this session

# Default configuration values
# These can be overridden by settings in the CONFIG_FILE
SERVER_IP="<server_ip>"     # Target iperf3 server IP address
DURATION=60                 # Duration of each test in seconds
MIN_BW=50                   # Minimum bandwidth for UDP tests (Mbps)
MAX_BW=450                 # Maximum bandwidth for UDP tests (Mbps)
IMIX_SIZES=(64 576 1500)   # Internet Mix (IMIX) packet sizes in bytes
                          # 64B: Voice/Control traffic
                          # 576B: Small data packets
                          # 1500B: Large data transfers/file downloads
IMIX_WEIGHTS=(50 30 20)    # Weight distribution for IMIX sizes (must sum to 100)
TCP_PERCENTAGE=30          # Percentage of tests that should use TCP
NUM_PROCESSES=10           # Number of concurrent iperf3 processes

# Global counters for test statistics
# These are declared as integers (-i) to ensure proper arithmetic
declare -i TOTAL_TESTS=0    # Counter for total number of tests run
declare -i FAILED_TESTS=0   # Counter for failed tests
START_TIME=$(date +%s)      # Script start time for duration calculation

# Error handling setup
set -euo pipefail          # Exit on error (-e), undefined vars (-u), pipe failures (-o pipefail)
trap cleanup EXIT          # Ensure cleanup runs on script exit
trap 'echo "Error on line $LINENO" | tee -a "${LOG_FILE}"' ERR  # Error line reporting

###################################################################################
# Configuration Management Functions
###################################################################################

# Loads configuration from external file if it exists
load_config() {
    if [[ -f "${CONFIG_FILE}" ]]; then
        source "${CONFIG_FILE}"
        log_message "Loaded configuration from ${CONFIG_FILE}"
    else
        log_message "No configuration file found, using defaults"
    fi
}

# Logging function with timestamp
# Parameters:
#   $1: Message to log
log_message() {
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[${timestamp}] $1" | tee -a "${LOG_FILE}"
}

###################################################################################
# Validation Functions
###################################################################################

# Validates IP address format
# Parameters:
#   $1: IP address to validate
# Returns:
#   0 if valid, 1 if invalid
validate_ip() {
    local ip=$1
    if [[ ! $ip =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        log_message "ERROR: Invalid IP address format: ${ip}"
        return 1
    fi
    return 0
}

# Checks for required dependencies (iperf3)
check_dependencies() {
    if ! command -v iperf3 &> /dev/null; then
        log_message "ERROR: iperf3 is not installed"
        exit 1
    fi
}

# Creates required directories with proper permissions
setup_directories() {
    mkdir -p "${LOG_DIR}"
    chmod 755 "${LOG_DIR}"  # rwxr-xr-x permissions
}

###################################################################################
# Test Configuration Functions
###################################################################################

# Selects IMIX packet size based on defined weights
# Returns:
#   Selected packet size in bytes
get_imix_packet_size() {
    local total_weight=0
    for weight in "${IMIX_WEIGHTS[@]}"; do
        total_weight=$((total_weight + weight))
    done
    
    local rand=$((RANDOM % total_weight))
    local cumulative_weight=0
    
    # Weighted random selection
    for i in "${!IMIX_WEIGHTS[@]}"; do
        cumulative_weight=$((cumulative_weight + IMIX_WEIGHTS[i]))
        if ((rand < cumulative_weight)); then
            echo "${IMIX_SIZES[i]}"
            return
        fi
    done
}

# Randomly selects protocol (TCP/UDP) based on TCP_PERCENTAGE
# Returns:
#   "TCP" or "UDP"
choose_protocol() {
    if [[ $TCP_PERCENTAGE -lt 0 || $TCP_PERCENTAGE -gt 100 ]]; then
        log_message "ERROR: Invalid TCP_PERCENTAGE: ${TCP_PERCENTAGE}"
        exit 1
    fi
    
    local rand=$((RANDOM % 100))
    if ((rand < TCP_PERCENTAGE)); then
        echo "TCP"
    else
        echo "UDP"
    fi
}

###################################################################################
# Network Testing Functions
###################################################################################

# Checks if target server is reachable
# Returns:
#   0 if server is reachable, 1 if not
check_server() {
    if ! ping -c 1 -W 2 "${SERVER_IP}" &> /dev/null; then
        log_message "ERROR: Cannot reach server ${SERVER_IP}"
        return 1
    fi
    return 0
}

# Starts a single iperf3 test process
# Parameters:
#   $1: Process ID (used for logging)
start_iperf_process() {
    local process_id=$1
    local protocol=$(choose_protocol)
    local test_id="${TIMESTAMP}_${process_id}"
    
    if [[ "$protocol" == "UDP" ]]; then
        # UDP test with random bandwidth and packet size
        local random_bw=$(shuf -i ${MIN_BW}-${MAX_BW} -n 1)
        local random_pkt_size=$(get_imix_packet_size)
        log_message "Starting UDP test ${test_id}: BW=${random_bw}Mbps, PKT=${random_pkt_size}B"
        
        # Run UDP test with error handling
        if ! iperf3 -c ${SERVER_IP} -u -b ${random_bw}M -l ${random_pkt_size} -t ${DURATION} \
            --logfile "${LOG_DIR}/iperf_${test_id}.log" 2>/dev/null; then
            log_message "ERROR: UDP test ${test_id} failed"
            ((FAILED_TESTS++))
        fi
    else
        # TCP test with default settings
        log_message "Starting TCP test ${test_id}"
        if ! iperf3 -c ${SERVER_IP} -t ${DURATION} \
            --logfile "${LOG_DIR}/iperf_${test_id}.log" 2>/dev/null; then
            log_message "ERROR: TCP test ${test_id} failed"
            ((FAILED_TESTS++))
        fi
    fi
    ((TOTAL_TESTS++))
}

###################################################################################
# Cleanup and Summary Functions
###################################################################################

# Cleanup function called on script exit
# Kills any remaining iperf3 processes and prints test summary
cleanup() {
    local exit_code=$?
    log_message "Cleaning up..."
    pkill -f iperf3 || true  # Kill any remaining iperf3 processes
    
    # Calculate and print test summary
    local end_time=$(date +%s)
    local duration=$((end_time - START_TIME))
    log_message "Test Summary:"
    log_message "Total Duration: ${duration} seconds"
    log_message "Total Tests: ${TOTAL_TESTS}"
    log_message "Failed Tests: ${FAILED_TESTS}"
    log_message "Success Rate: $(( (TOTAL_TESTS - FAILED_TESTS) * 100 / TOTAL_TESTS ))%"
    
    exit ${exit_code}
}

###################################################################################
# Main Program
###################################################################################

# Main function coordinating the entire test suite
main() {
    # Initial setup
    setup_directories
    check_dependencies
    load_config
    
    # Validate configuration
    if ! validate_ip "${SERVER_IP}"; then
        exit 1
    fi
    
    # Log test parameters
    log_message "Starting network test suite"
    log_message "Server IP: ${SERVER_IP}"
    log_message "Duration: ${DURATION} seconds"
    log_message "Processes: ${NUM_PROCESSES}"
    
    # Main test loop
    while true; do
        # Check server availability before starting tests
        if ! check_server; then
            sleep 60  # Wait 60 seconds before retry if server is unreachable
            continue
        fi
        
        # Start concurrent test processes
        for ((i = 1; i <= NUM_PROCESSES; i++)); do
            start_iperf_process $i
        done
        
        wait  # Wait for all processes to complete
        log_message "Completed test batch"
    done
}

# Script entry point
main
