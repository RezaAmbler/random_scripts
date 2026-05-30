#!/usr/bin/env python3
"""Whois checker — look up and parse WHOIS records for IP prefixes."""
import subprocess
import ipaddress
import re

def whois_lookup(ip_prefix):
    try:
        result = subprocess.run(['whois', ip_prefix], capture_output=True, text=True, timeout=10)
        return result.stdout
    except subprocess.TimeoutExpired:
        return f"Timeout occurred for {ip_prefix}"
    except Exception as e:
        return f"Error occurred for {ip_prefix}: {str(e)}"

def parse_whois_output(output):
    patterns = {
        'OrgName': r'OrgName:\s*(.*)',
        'OrgId': r'OrgId:\s*(.*)',
        'NetName': r'NetName:\s*(.*)',
        'NetHandle': r'NetHandle:\s*(.*)',
        'CIDR': r'CIDR:\s*(.*)',
        'Description': r'descr:\s*(.*)'
    }
    
    results = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, output)
        results[key] = match.group(1) if match else 'N/A'
    
    return results

def print_table_row(data, widths):
    row = '| '
    for item, width in zip(data, widths):
        row += f'{item:<{width}} | '
    print(row)

def print_separator(widths):
    print('+' + '+'.join('-' * (w + 2) for w in widths) + '+')

def main():
    input_file = 'ip_prefixes.txt'
    output_file = 'whois_results.txt'

    with open(input_file, 'r') as f:
        ip_prefixes = [line.strip() for line in f if line.strip()]

    # Define table headers and their initial widths
    headers = ['Prefix', 'OrgName', 'OrgId', 'NetName', 'NetHandle', 'CIDR', 'Description']
    widths = [len(header) for header in headers]

    # First pass to determine column widths
    results = []
    for prefix in ip_prefixes:
        try:
            ipaddress.ip_network(prefix)
            whois_result = whois_lookup(prefix)
            parsed_result = parse_whois_output(whois_result)
            parsed_result['Prefix'] = prefix
            results.append(parsed_result)
            
            for i, key in enumerate(headers):
                widths[i] = max(widths[i], len(str(parsed_result.get(key, 'N/A'))))
        except ValueError:
            print(f"Invalid IP prefix: {prefix}")

    # Print table header
    print_separator(widths)
    print_table_row(headers, widths)
    print_separator(widths)

    # Print table content and write full results to file
    with open(output_file, 'w') as f:
        for result in results:
            print_table_row([result.get(key, 'N/A') for key in headers], widths)
            
            f.write(f"WHOIS lookup for {result['Prefix']}:\n")
            f.write(whois_lookup(result['Prefix']))
            f.write("\n" + "="*50 + "\n\n")

    print_separator(widths)
    print(f"\nFull WHOIS results saved to {output_file}")

if __name__ == "__main__":
    main()
