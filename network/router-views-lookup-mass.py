#!/usr/bin/env python3
"""
BGP Prefix Visibility Checker
Queries multiple route servers to check BGP prefix visibility and AS paths.
"""

import argparse
import asyncio
import ipaddress
import logging
import re
import sys
import time
import yaml
from collections import defaultdict
from typing import Dict, List, Optional, Tuple, Set

try:
    import asyncssh
    SSH_AVAILABLE = True
except ImportError:
    SSH_AVAILABLE = False
    print("Warning: asyncssh not available. SSH connections will be disabled.")

try:
    import telnetlib3
    TELNET_AVAILABLE = True
except ImportError:
    TELNET_AVAILABLE = False
    print("Warning: telnetlib3 not available. Telnet connections will be disabled.")
    print("Install with: pip install telnetlib3")

try:
    from rich.console import Console
    from rich.text import Text
    from rich.table import Table
    RICH_AVAILABLE = True
    console = Console()
except ImportError:
    RICH_AVAILABLE = False
    console = None

# Hard-coded watched ASNs (Lumen, Zayo, AT&T)
WATCHED_ASNS = [3356, 6461, 7018]
WATCHED_ASN_NAMES = {
    3356: "Lumen",
    6461: "Zayo", 
    7018: "AT&T"
}


class BGPResult:
    """Container for BGP query results"""
    def __init__(self, server_name: str, success: bool, raw_output: str = "", 
                 as_paths: List[str] = None, error: str = ""):
        self.server_name = server_name
        self.success = success
        self.raw_output = raw_output
        self.as_paths = as_paths or []
        self.error = error
        self.watched_asns_found = set()  # Track which watched ASNs were found
        
        # Analyze AS paths for watched ASNs
        for path in self.as_paths:
            for asn_str in path.split():
                try:
                    asn = int(asn_str)
                    if asn in WATCHED_ASNS:
                        self.watched_asns_found.add(asn)
                except ValueError:
                    continue


class ColorHelper:
    """Handle colored output with fallback to plain text"""
    
    def __init__(self, use_color: bool = True):
        self.use_color = use_color and RICH_AVAILABLE
        if self.use_color:
            self.console = Console(force_terminal=True)
        else:
            self.console = None
    
    def green(self, text: str) -> str:
        """Green text for success/present"""
        if self.use_color:
            return f"[green]{text}[/green]"
        return text
    
    def red(self, text: str) -> str:
        """Red text for errors/missing"""
        if self.use_color:
            return f"[red]{text}[/red]"
        return text
    
    def yellow(self, text: str) -> str:
        """Yellow text for warnings/no routes"""
        if self.use_color:
            return f"[yellow]{text}[/yellow]"
        return text
    
    def cyan(self, text: str) -> str:
        """Cyan text for headers"""
        if self.use_color:
            return f"[cyan]{text}[/cyan]"
        return text
    
    def bold(self, text: str) -> str:
        """Bold text for emphasis"""
        if self.use_color:
            return f"[bold]{text}[/bold]"
        return text
    
    def highlight_watched_asns(self, as_path: str, watched_asns: List[int]) -> str:
        """Highlight watched ASNs in green within an AS path"""
        if not self.use_color:
            return as_path
        
        # Split path and highlight watched ASNs
        parts = as_path.split()
        highlighted_parts = []
        
        for part in parts:
            try:
                asn = int(part)
                if asn in watched_asns:
                    highlighted_parts.append(f"[green]{part}[/green]")
                else:
                    highlighted_parts.append(part)
            except ValueError:
                highlighted_parts.append(part)
        
        return " ".join(highlighted_parts)
    
    def print(self, text: str = "", **kwargs):
        """Print with optional rich formatting"""
        if self.use_color and self.console:
            self.console.print(text, **kwargs)
        else:
            # Strip rich markup for plain text
            import re
            clean_text = re.sub(r'\[/?[a-zA-Z0-9_]+\]', '', text)
            print(clean_text, **kwargs)


class BGPParser:
    """Parse BGP output from different router types"""
    
    @staticmethod
    def parse_cisco_output(output: str) -> List[str]:
        """Parse Cisco IOS BGP output"""
        as_paths = []
        lines = output.split('\n')
        
        for line in lines:
            line = line.strip()
            # Look for lines with BGP paths (contain status codes and AS path)
            # Example: "*>   209.196.222.0    4.68.4.46                0             0 3356 12213 398172 i"
            if re.match(r'^[*>irshfNxacSRb\s]+\d+\.\d+\.\d+\.\d+(/\d+)?', line):
                parts = line.split()
                if len(parts) >= 6:
                    # AS path typically starts after next-hop, metric, locprf, weight
                    path_start = -1
                    for i, part in enumerate(parts):
                        if part.isdigit() and i > 2:  # Look for first AS number after basic fields
                            path_start = i
                            break
                    
                    if path_start > 0:
                        # Extract AS path (everything before origin code i/e/?)
                        path_parts = []
                        for j in range(path_start, len(parts)):
                            if parts[j] in ['i', 'e', '?']:
                                break
                            if parts[j].isdigit() or '{' in parts[j]:
                                path_parts.append(parts[j])
                        
                        if path_parts:
                            as_path = ' '.join(path_parts)
                            as_paths.append(as_path)
        
        return as_paths
    
    @staticmethod
    def parse_quagga_output(output: str) -> List[str]:
        """Parse Quagga/FRR BGP output"""
        as_paths = []
        lines = output.split('\n')
        
        for line in lines:
            line = line.strip()
            # Look for lines with BGP paths
            # Example: "* i209.196.222.0    62.69.146.139            0    100      0 6461 12213 398172 i"
            if re.match(r'^[*>irshfNxacSRb\s]+\d+\.\d+\.\d+\.\d+(/\d+)?', line):
                parts = line.split()
                if len(parts) >= 7:
                    # In Quagga format, AS path is typically after weight column
                    path_start = -1
                    weight_found = False
                    for i, part in enumerate(parts):
                        if weight_found and part.isdigit():
                            path_start = i
                            break
                        if part.isdigit() and i > 4:  # Weight column
                            weight_found = True
                    
                    if path_start > 0:
                        path_parts = []
                        for j in range(path_start, len(parts)):
                            if parts[j] in ['i', 'e', '?']:
                                break
                            if parts[j].isdigit() or '{' in parts[j]:
                                path_parts.append(parts[j])
                        
                        if path_parts:
                            as_path = ' '.join(path_parts)
                            as_paths.append(as_path)
        
        return as_paths
    
    @staticmethod
    def parse_bird_output(output: str) -> List[str]:
        """Parse BIRD BGP output"""
        as_paths = []
        lines = output.split('\n')
        
        for line in lines:
            line = line.strip()
            # BIRD output format varies, look for AS path patterns
            if 'BGP.as_path:' in line:
                match = re.search(r'BGP\.as_path:\s*(.+)', line)
                if match:
                    as_path = match.group(1).strip()
                    as_paths.append(as_path)
            elif re.search(r'\d+\.\d+\.\d+\.\d+(/\d+)?.*\[AS\d+', line):
                # Alternative BIRD format
                match = re.search(r'\[AS(\d+[^\]]*)\]', line)
                if match:
                    as_path = match.group(1)
                    as_paths.append(as_path)
        
        return as_paths


class RouteServerClient:
    """Handle connections to route servers"""
    
    def __init__(self, timeout: int = 30, debug: bool = False):
        self.timeout = timeout
        self.debug = debug
        if debug:
            logging.basicConfig(level=logging.DEBUG)
        self.logger = logging.getLogger(__name__)
    
    def _debug_log(self, message: str):
        """Log debug message"""
        if self.debug:
            print(f"DEBUG: {message}")
            self.logger.debug(message)
    
    async def query_telnet(self, server: Dict, prefix: str) -> BGPResult:
        """Query route server via Telnet using telnetlib3"""
        if not TELNET_AVAILABLE:
            return BGPResult(
                server_name=server['name'],
                success=False,
                error="Telnet not available (telnetlib3 not installed)"
            )
        
        self._debug_log(f"Connecting to {server['name']} at {server['host']}")
        
        try:
            # Connect using telnetlib3
            self._debug_log(f"Opening telnet connection to {server['host']}:23")
            reader, writer = await asyncio.wait_for(
                telnetlib3.open_connection(server['host'], 23),
                timeout=self.timeout
            )
            self._debug_log(f"Connected to {server['host']}")
            
            # Handle login sequence
            if server.get('username') and server['username'] != 'none':
                self._debug_log(f"Waiting for login prompt...")
                try:
                    data = await asyncio.wait_for(reader.readuntil(b'login:'), timeout=10)
                    self._debug_log(f"Received login prompt, sending username: {server['username']}")
                    writer.write(server['username'] + '\n')
                    await writer.drain()
                except asyncio.TimeoutError:
                    self._debug_log("Timeout waiting for login prompt, trying to send username anyway")
                    writer.write(server['username'] + '\n')
                    await writer.drain()
            
            if server.get('password') and server['password'] != 'none':
                self._debug_log(f"Waiting for password prompt...")
                try:
                    data = await asyncio.wait_for(reader.readuntil(b'Password:'), timeout=10)
                    self._debug_log(f"Received password prompt, sending password")
                except asyncio.TimeoutError:
                    self._debug_log("Timeout waiting for password prompt, trying to send password anyway")
                
                writer.write(server['password'] + '\n')
                await writer.drain()
            
            # Wait for command prompt
            self._debug_log("Waiting for command prompt...")
            await asyncio.sleep(3)
            
            # Clear any initial output
            try:
                initial_data = await asyncio.wait_for(reader.read(8192), timeout=2)
                self._debug_log(f"Cleared initial data: {initial_data[:200]}...")
            except asyncio.TimeoutError:
                self._debug_log("No initial data to clear")
            
            # Send command
            command = server['command'].replace('<prefix>', prefix)
            self._debug_log(f"Sending command: {command}")
            writer.write(command + '\n')
            await writer.drain()
            
            # Read response
            self._debug_log("Reading command response...")
            await asyncio.sleep(4)  # Give more time for command to execute
            
            output_parts = []
            total_bytes = 0
            try:
                while True:
                    data = await asyncio.wait_for(reader.read(4096), timeout=3)
                    if not data:
                        break
                    output_parts.append(data)
                    total_bytes += len(data)
                    self._debug_log(f"Read {len(data)} bytes, total: {total_bytes}")
            except asyncio.TimeoutError:
                self._debug_log(f"Finished reading (timeout), total bytes: {total_bytes}")
            
            # Close connection gracefully
            self._debug_log("Closing connection...")
            writer.close()
            
            # Try newer method first, fall back to older method
            try:
                if hasattr(writer, 'wait_closed'):
                    await writer.wait_closed()
                else:
                    # telnetlib3 might use a different method
                    await asyncio.sleep(0.1)  # Give it a moment to close
            except Exception as e:
                self._debug_log(f"Warning during connection close: {e}")
            
            # Combine output
            output = ''.join(output_parts)
            self._debug_log(f"Total output length: {len(output)} characters")
            
            if self.debug and output:
                print(f"\nDEBUG: Raw output from {server['name']}:")
                print("-" * 50)
                print(output[:1000] + ("..." if len(output) > 1000 else ""))
                print("-" * 50)
            
            # Parse output based on router type
            router_type = server.get('router_type', 'cisco').lower()
            self._debug_log(f"Parsing output as {router_type} format")
            
            if router_type == 'cisco':
                as_paths = BGPParser.parse_cisco_output(output)
            elif router_type in ['quagga', 'frr']:
                as_paths = BGPParser.parse_quagga_output(output)
            elif router_type == 'bird':
                as_paths = BGPParser.parse_bird_output(output)
            else:
                as_paths = BGPParser.parse_cisco_output(output)  # Default to Cisco
            
            self._debug_log(f"Found {len(as_paths)} AS paths: {as_paths}")
            
            return BGPResult(
                server_name=server['name'],
                success=True,
                raw_output=output,
                as_paths=as_paths
            )
            
        except Exception as e:
            self._debug_log(f"Exception in telnet query: {type(e).__name__}: {str(e)}")
            import traceback
            if self.debug:
                traceback.print_exc()
            
            return BGPResult(
                server_name=server['name'],
                success=False,
                error=f"Telnet connection failed: {type(e).__name__}: {str(e)}"
            )
    
    async def query_ssh(self, server: Dict, prefix: str) -> BGPResult:
        """Query route server via SSH"""
        if not SSH_AVAILABLE:
            return BGPResult(
                server_name=server['name'],
                success=False,
                error="SSH not available (asyncssh not installed)"
            )
        
        try:
            command = server['command'].replace('<prefix>', prefix)
            
            # SSH connection parameters
            connect_params = {
                'host': server['host'],
                'username': server.get('username'),
                'password': server.get('password') if server.get('password') != 'none' else None,
                'known_hosts': None,  # Skip host key checking
                'client_keys': None,  # No key authentication
            }
            
            async with asyncssh.connect(**connect_params) as conn:
                result = await conn.run(command, timeout=self.timeout)
                output = result.stdout
                
                # Parse output based on router type
                router_type = server.get('router_type', 'cisco').lower()
                if router_type == 'cisco':
                    as_paths = BGPParser.parse_cisco_output(output)
                elif router_type in ['quagga', 'frr']:
                    as_paths = BGPParser.parse_quagga_output(output)
                elif router_type == 'bird':
                    as_paths = BGPParser.parse_bird_output(output)
                else:
                    as_paths = BGPParser.parse_cisco_output(output)
                
                return BGPResult(
                    server_name=server['name'],
                    success=True,
                    raw_output=output,
                    as_paths=as_paths
                )
                
        except Exception as e:
            return BGPResult(
                server_name=server['name'],
                success=False,
                error=f"SSH connection failed: {str(e)}"
            )


def load_route_servers(config_file: str) -> List[Dict]:
    """Load route server configuration from YAML file"""
    try:
        with open(config_file, 'r') as f:
            config = yaml.safe_load(f)
            return config.get('route_servers', [])
    except FileNotFoundError:
        print(f"Error: Configuration file '{config_file}' not found.")
        print("Creating a sample configuration file...")
        create_sample_config(config_file)
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"Error parsing YAML file: {e}")
        sys.exit(1)


def create_sample_config(filename: str):
    """Create a sample configuration file"""
    sample_config = {
        'route_servers': [
            {
                'name': 'RouteViews Oregon',
                'host': 'route-views.routeviews.org',
                'access': 'telnet',
                'username': 'rviews',
                'password': 'none',
                'router_type': 'cisco',
                'command': 'show ip bgp <prefix> longer-prefixes'
            },
            {
                'name': 'Hurricane Electric',
                'host': 'route-server.he.net',
                'access': 'telnet',
                'username': 'none',
                'password': 'rviews',
                'router_type': 'quagga',
                'command': 'show ip bgp <prefix> longer-prefixes'
            }
        ]
    }
    
    with open(filename, 'w') as f:
        yaml.dump(sample_config, f, default_flow_style=False, indent=2)
    
    print(f"Sample configuration created: {filename}")
    print("Edit this file to add your route servers, then run the script again.")


async def run_concurrent_lookups_batch(prefixes: List[str], servers: List[Dict], debug: bool = False) -> Dict[str, List[BGPResult]]:
    """Run BGP lookups for multiple prefixes across all route servers"""
    client = RouteServerClient(debug=debug)

    # Group tasks by server to avoid overwhelming any single server
    all_results = {}

    total = len(prefixes)
    start = time.time()
    if debug:
        print(f"DEBUG: Processing {total} prefixes across {len(servers)} servers...")

    for i, prefix in enumerate(prefixes, 1):
        elapsed = time.time() - start
        # Progress: prefix position, server count, and elapsed time.
        print(f"[{i}/{total}] Querying {prefix} across {len(servers)} server(s) "
              f"({elapsed:.0f}s elapsed)")

        # Keep tasks and their owning servers in lockstep — skipped servers
        # (unknown access type) must not shift the index used to attribute
        # exceptions back to a server below.
        tasks = []
        task_servers = []
        for server in servers:
            if server['access'].lower() == 'telnet':
                task = client.query_telnet(server, prefix)
            elif server['access'].lower() == 'ssh':
                task = client.query_ssh(server, prefix)
            else:
                if debug:
                    print(f"Warning: Unknown access type '{server['access']}' for {server['name']}")
                continue

            tasks.append(task)
            task_servers.append(server)

        if not tasks:
            print("No valid route servers configured.")
            continue

        # Run queries for this prefix
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Handle any exceptions that occurred
        clean_results = []
        for j, result in enumerate(results):
            if isinstance(result, Exception):
                clean_results.append(BGPResult(
                    server_name=task_servers[j]['name'],
                    success=False,
                    error=f"Query exception: {str(result)}"
                ))
            else:
                clean_results.append(result)

        all_results[prefix] = clean_results

        # Small delay between prefixes to be respectful to route servers
        if i < total:
            await asyncio.sleep(1)

    print(f"Completed {total} prefix(es) across {len(servers)} server(s) "
          f"in {time.time() - start:.0f}s")
    return all_results


def summarize_batch_results(batch_results: Dict[str, List[BGPResult]], watched_asns: List[int]) -> Dict:
    """Summarize and analyze BGP lookup results for multiple prefixes"""
    
    # Aggregate data across all prefixes
    server_stats = {}  # server_name -> {visible: count, failed: count, no_routes: count, watched_asns: set}
    prefix_stats = {'total': 0, 'visible_somewhere': 0, 'not_visible_anywhere': 0}
    watched_asn_stats = {asn: {'prefixes_seen': 0, 'servers_seeing': set()} for asn in watched_asns}
    all_unique_paths = set()
    
    # New data structures for detailed analysis
    invisible_prefixes = set()
    asn_prefix_visibility = {asn: {'prefixes': set(), 'server_details': defaultdict(set)} for asn in watched_asns}
    
    for prefix, results in batch_results.items():
        prefix_stats['total'] += 1
        prefix_visible = False
        
        for result in results:
            server_name = result.server_name
            
            # Initialize server stats if needed
            if server_name not in server_stats:
                server_stats[server_name] = {
                    'visible': 0, 'failed': 0, 'no_routes': 0, 
                    'watched_asns': {asn: 0 for asn in watched_asns}
                }
            
            if not result.success:
                server_stats[server_name]['failed'] += 1
            elif not result.as_paths:
                server_stats[server_name]['no_routes'] += 1
            else:
                server_stats[server_name]['visible'] += 1
                prefix_visible = True
                
                # Track unique AS paths (but don't show them all for batch mode)
                all_unique_paths.update(result.as_paths)
                
                # Check for watched ASNs in this result
                for path in result.as_paths:
                    for asn_str in path.split():
                        try:
                            asn = int(asn_str)
                            if asn in watched_asns:
                                server_stats[server_name]['watched_asns'][asn] += 1
                                watched_asn_stats[asn]['servers_seeing'].add(server_name)
                                # Track detailed ASN visibility
                                asn_prefix_visibility[asn]['prefixes'].add(prefix)
                                asn_prefix_visibility[asn]['server_details'][prefix].add(server_name)
                        except ValueError:
                            continue
        
        # Update prefix visibility stats
        if prefix_visible:
            prefix_stats['visible_somewhere'] += 1
        else:
            prefix_stats['not_visible_anywhere'] += 1
            invisible_prefixes.add(prefix)
        
        # Update watched ASN prefix counts
        prefix_watched_asns = set()
        for result in results:
            if result.success and result.as_paths:
                for path in result.as_paths:
                    for asn_str in path.split():
                        try:
                            asn = int(asn_str)
                            if asn in watched_asns:
                                prefix_watched_asns.add(asn)
                        except ValueError:
                            continue
        
        for asn in prefix_watched_asns:
            watched_asn_stats[asn]['prefixes_seen'] += 1
    
    return {
        'server_stats': server_stats,
        'prefix_stats': prefix_stats,
        'watched_asn_stats': watched_asn_stats,
        'unique_path_count': len(all_unique_paths),
        'batch_results': batch_results,
        'invisible_prefixes': invisible_prefixes,
        'asn_prefix_visibility': asn_prefix_visibility
    }


def display_batch_summary(summary: Dict, prefixes: List[str], watched_asns: List[int], 
                         verbose: bool = False, detail: bool = False, use_color: bool = True):
    """Display batch results summary with color coding"""
    color = ColorHelper(use_color)
    
    color.print(f"\n" + "="*60)
    if len(prefixes) == 1:
        color.print(f"BGP PREFIX VISIBILITY REPORT: {prefixes[0]}")
    else:
        color.print(f"BGP BATCH VISIBILITY REPORT: {len(prefixes)} prefixes")
    color.print("="*60)
    
    # Prefix-level summary for batch mode
    if len(prefixes) > 1:
        prefix_stats = summary['prefix_stats']
        color.print(f"Prefix Summary:")
        color.print(f"  Total prefixes: {prefix_stats['total']}")
        color.print(f"  Visible somewhere: {color.green(str(prefix_stats['visible_somewhere']))}")
        color.print(f"  Not visible anywhere: {color.red(str(prefix_stats['not_visible_anywhere']))}")
        color.print(f"  Unique AS paths found: {summary['unique_path_count']}")
        color.print()
    
    # Server performance summary
    color.print("Server Performance Summary:")
    server_stats = summary['server_stats']
    
    if use_color and RICH_AVAILABLE:
        # Rich table for server stats
        table = Table()
        table.add_column("Server", style="bold")
        table.add_column("Visible", style="green")
        table.add_column("No Routes", style="yellow") 
        table.add_column("Failed", style="red")
        
        # Add watched ASN columns
        for asn in watched_asns:
            table.add_column(f"{asn}", style="cyan")
        
        for server_name, stats in server_stats.items():
            row = [
                server_name,
                str(stats['visible']),
                str(stats['no_routes']),
                str(stats['failed'])
            ]
            
            # Add watched ASN counts
            for asn in watched_asns:
                count = stats['watched_asns'][asn]
                if count > 0:
                    row.append(Text(str(count), style="green"))
                else:
                    row.append(Text("0", style="dim"))
            
            table.add_row(*row)
        
        color.console.print(table)
    else:
        # Plain text table
        headers = ["Server", "Visible", "No Routes", "Failed"] + [str(asn) for asn in watched_asns]
        col_widths = [25, 8, 10, 8] + [6] * len(watched_asns)
        
        header_line = ""
        for i, header in enumerate(headers):
            header_line += f"{header:<{col_widths[i]}}"
        color.print(header_line)
        color.print("-" * len(header_line))
        
        for server_name, stats in server_stats.items():
            line = f"{server_name:<{col_widths[0]}}"
            line += f"{color.green(str(stats['visible'])):<{col_widths[1]}}"
            line += f"{color.yellow(str(stats['no_routes'])):<{col_widths[2]}}" 
            line += f"{color.red(str(stats['failed'])):<{col_widths[3]}}"
            
            for i, asn in enumerate(watched_asns):
                count = stats['watched_asns'][asn]
                if count > 0:
                    count_str = color.green(str(count))
                else:
                    count_str = "0"
                line += f"{count_str:<{col_widths[4+i]}}"
            
            color.print(line)
    
    # Watched ASN summary
    color.print(f"\nWatched ASN Summary:")
    watched_stats = summary['watched_asn_stats']
    for asn in watched_asns:
        asn_name = WATCHED_ASN_NAMES.get(asn, f"AS{asn}")
        stats = watched_stats[asn]
        prefixes_seen = stats['prefixes_seen']
        servers_count = len(stats['servers_seeing'])
        
        if prefixes_seen > 0:
            status = color.green(f"✅ {prefixes_seen}/{len(prefixes)} prefixes, {servers_count} servers")
        else:
            status = color.red("❌ NOT seen")
        
        color.print(f"  • {asn_name} ({asn}): {status}")
    
    # DETAIL SECTION - New functionality
    if detail:
        color.print(f"\n" + "="*60)
        color.print(color.bold("DETAILED ANALYSIS"))
        color.print("="*60)
        
        # 1. Prefixes Not Visible Anywhere
        invisible_prefixes = summary['invisible_prefixes']
        color.print(f"\n{color.cyan('Prefixes Not Visible Anywhere:')}")
        if invisible_prefixes:
            color.print(f"  {color.red(f'Found {len(invisible_prefixes)} invisible prefixes:')}")
            for prefix in sorted(invisible_prefixes):
                color.print(f"    • {color.red(prefix)}")
        else:
            color.print(f"  {color.green('✅ All prefixes visible on at least one route server')}")
        
        # 2. ASN Visibility Details
        asn_visibility = summary['asn_prefix_visibility']
        all_prefixes_set = set(prefixes)
        
        for asn in watched_asns:
            asn_name = WATCHED_ASN_NAMES.get(asn, f"AS{asn}")
            color.print(f"\n{color.cyan(f'{asn_name} ({asn}) Visibility Analysis:')}")
            
            seen_prefixes = asn_visibility[asn]['prefixes']
            missing_prefixes = all_prefixes_set - seen_prefixes
            
            # Show prefixes seen on this ASN
            color.print(f"  {color.bold('Prefixes seen on')} {color.green(f'AS{asn}')}:")
            if seen_prefixes:
                for prefix in sorted(seen_prefixes):
                    # Show which servers saw this prefix on this ASN
                    servers_for_prefix = asn_visibility[asn]['server_details'][prefix]
                    servers_str = ", ".join(sorted(servers_for_prefix))
                    color.print(f"    • {color.green(prefix)} {color.yellow(f'[{servers_str}]')}")
            else:
                color.print(f"    {color.red('❌ None')}")
            
            # Show missing prefixes
            color.print(f"  {color.bold('Missing from')} {color.red(f'AS{asn}')}:")
            if missing_prefixes:
                for prefix in sorted(missing_prefixes):
                    color.print(f"    • {color.red(prefix)}")
            else:
                color.print(f"    {color.green('✅ All prefixes present')}")
    
    # Verbose output for single prefix or if specifically requested
    if verbose and len(prefixes) == 1:
        color.print(f"\n" + "-"*60)
        color.print("VERBOSE OUTPUT - Raw responses:")
        color.print("-"*60)
        
        prefix = prefixes[0]
        results = summary['batch_results'][prefix]
        
        for result in results:
            color.print(f"\n[{result.server_name}]")
            if result.success and result.raw_output:
                output_preview = result.raw_output[:1000] + "..." if len(result.raw_output) > 1000 else result.raw_output
                color.print(output_preview)
            else:
                color.print(f"Error: {result.error}")
    elif verbose and len(prefixes) > 1:
        color.print(f"\nVerbose mode not shown for batch queries with {len(prefixes)} prefixes.")
        color.print("Use single prefix mode for detailed output.")


def save_batch_results(summary: Dict, prefixes: List[str], filename: str, detail: bool = False):
    """Save batch results to file"""
    try:
        with open(filename, 'w') as f:
            if len(prefixes) == 1:
                f.write(f"BGP Prefix Visibility Report: {prefixes[0]}\n")
            else:
                f.write(f"BGP Batch Visibility Report: {len(prefixes)} prefixes\n")
            f.write("="*60 + "\n\n")
            
            if len(prefixes) > 1:
                prefix_stats = summary['prefix_stats']
                f.write(f"Prefix Summary:\n")
                f.write(f"- Total prefixes: {prefix_stats['total']}\n")
                f.write(f"- Visible somewhere: {prefix_stats['visible_somewhere']}\n")
                f.write(f"- Not visible anywhere: {prefix_stats['not_visible_anywhere']}\n")
                f.write(f"- Unique AS paths found: {summary['unique_path_count']}\n\n")
            
            f.write("Server Performance Summary:\n")
            f.write("-" * 40 + "\n")
            
            server_stats = summary['server_stats']
            for server_name, stats in server_stats.items():
                f.write(f"\n{server_name}:\n")
                f.write(f"  Visible routes: {stats['visible']}\n")
                f.write(f"  No routes: {stats['no_routes']}\n")
                f.write(f"  Failed queries: {stats['failed']}\n")
                f.write(f"  Watched ASNs: ")
                asn_counts = [f"{asn}:{stats['watched_asns'][asn]}" for asn in WATCHED_ASNS]
                f.write(", ".join(asn_counts) + "\n")
            
            f.write(f"\nWatched ASN Summary:\n")
            watched_stats = summary['watched_asn_stats']
            for asn in WATCHED_ASNS:
                asn_name = WATCHED_ASN_NAMES.get(asn, f"AS{asn}")
                stats = watched_stats[asn]
                f.write(f"- {asn_name} ({asn}): {stats['prefixes_seen']}/{len(prefixes)} prefixes, ")
                f.write(f"{len(stats['servers_seeing'])} servers\n")
            
            # Detail section in saved output
            if detail:
                f.write(f"\n" + "="*60 + "\n")
                f.write("DETAILED ANALYSIS\n")
                f.write("="*60 + "\n")
                
                # Invisible prefixes
                invisible_prefixes = summary['invisible_prefixes']
                f.write(f"\nPrefixes Not Visible Anywhere:\n")
                if invisible_prefixes:
                    f.write(f"Found {len(invisible_prefixes)} invisible prefixes:\n")
                    for prefix in sorted(invisible_prefixes):
                        f.write(f"  - {prefix}\n")
                else:
                    f.write("All prefixes visible on at least one route server\n")
                
                # ASN visibility details
                asn_visibility = summary['asn_prefix_visibility']
                all_prefixes_set = set(prefixes)
                
                for asn in WATCHED_ASNS:
                    asn_name = WATCHED_ASN_NAMES.get(asn, f"AS{asn}")
                    f.write(f"\n{asn_name} ({asn}) Visibility Analysis:\n")
                    
                    seen_prefixes = asn_visibility[asn]['prefixes']
                    missing_prefixes = all_prefixes_set - seen_prefixes
                    
                    f.write(f"Prefixes seen on AS{asn}:\n")
                    if seen_prefixes:
                        for prefix in sorted(seen_prefixes):
                            servers_for_prefix = asn_visibility[asn]['server_details'][prefix]
                            servers_str = ", ".join(sorted(servers_for_prefix))
                            f.write(f"  - {prefix} [{servers_str}]\n")
                    else:
                        f.write(f"  - None\n")
                    
                    f.write(f"Missing from AS{asn}:\n")
                    if missing_prefixes:
                        for prefix in sorted(missing_prefixes):
                            f.write(f"  - {prefix}\n")
                    else:
                        f.write(f"  - All prefixes present\n")
            
            # Detailed results for single prefix
            if len(prefixes) == 1:
                f.write(f"\nDetailed Results:\n")
                f.write("-" * 40 + "\n")
                
                prefix = prefixes[0]
                results = summary['batch_results'][prefix]
                
                for result in results:
                    f.write(f"\n[{result.server_name}]\n")
                    if result.success:
                        if result.as_paths:
                            f.write(f"AS Paths: {', '.join(result.as_paths)}\n")
                        else:
                            f.write("Status: No routes found\n")
                        f.write(f"Raw Output:\n{result.raw_output}\n")
                    else:
                        f.write(f"Error: {result.error}\n")
                    f.write("-" * 40 + "\n")
        
        print(f"\nResults saved to: {filename}")
    except Exception as e:
        print(f"Error saving results: {e}")


def is_valid_prefix(value: str) -> bool:
    """True if value is a valid IP address or CIDR network (v4 or v6)."""
    try:
        ipaddress.ip_network(value, strict=False)
        return True
    except ValueError:
        try:
            ipaddress.ip_address(value)
            return True
        except ValueError:
            return False


def get_prefix_input(args) -> List[str]:
    """Get prefix(es) from command line args, file, or user input"""
    prefixes = []

    # Check for file input first
    if args.file:
        try:
            with open(args.file, 'r') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if line and not line.startswith('#'):  # Skip empty lines and comments
                        if is_valid_prefix(line):
                            prefixes.append(line)
                        else:
                            print(f"Warning: Skipping invalid prefix on line {line_num}: {line}")

            if not prefixes:
                print(f"Error: No valid prefixes found in file {args.file}")
                sys.exit(1)

            print(f"Loaded {len(prefixes)} prefixes from {args.file}")
            return prefixes

        except FileNotFoundError:
            print(f"Error: File {args.file} not found")
            sys.exit(1)
        except Exception as e:
            print(f"Error reading file {args.file}: {e}")
            sys.exit(1)

    # Single prefix from command line
    if args.prefix:
        if not is_valid_prefix(args.prefix):
            print(f"Error: '{args.prefix}' is not a valid IP prefix")
            sys.exit(1)
        return [args.prefix]

    # Interactive input for single prefix
    while True:
        prefix = input("Enter prefix to look up (e.g., 1.2.3.0/24): ").strip()
        if prefix:
            if is_valid_prefix(prefix):
                return [prefix]
            else:
                print("Please enter a valid IP prefix (e.g., 192.168.1.0/24)")
        else:
            print("Prefix cannot be empty.")


def parse_watched_asns(watch_arg: str) -> List[int]:
    """Parse comma-separated ASN list from command line"""
    try:
        return [int(asn.strip()) for asn in watch_arg.split(',')]
    except ValueError as e:
        print(f"Error parsing watched ASNs: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Check BGP prefix visibility across multiple route servers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --prefix 1.2.3.0/24
  %(prog)s --prefix 192.168.1.0/24 --verbose
  %(prog)s --config my_servers.yaml --save results.txt
  %(prog)s --prefix 1.2.3.0/24 --watch 174,701,3356
  %(prog)s --file prefixes.txt --save batch_results.txt
  %(prog)s --file prefixes.txt --detail --save detailed_results.txt
        """
    )
    
    parser.add_argument('--prefix', '-p', 
                       help='BGP prefix to look up (e.g., 1.2.3.0/24)')
    parser.add_argument('--file', '-f', metavar='FILE',
                       help='File containing list of prefixes to check (one per line)')
    parser.add_argument('--config', '-c', default='route_servers.yaml',
                       help='YAML configuration file (default: route_servers.yaml)')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Show detailed raw output from each server')
    parser.add_argument('--detail', action='store_true',
                       help='Show detailed analysis including invisible prefixes and per-ASN visibility')
    parser.add_argument('--save', '-s', metavar='FILE',
                       help='Save full results to file')
    parser.add_argument('--timeout', '-t', type=int, default=30,
                       help='Connection timeout in seconds (default: 30)')
    parser.add_argument('--debug', '-d', action='store_true',
                       help='Enable debug output to see connection details')
    parser.add_argument('--no-color', action='store_true',
                       help='Disable colored output (useful for piping to files)')
    parser.add_argument('--watch', metavar='ASN1,ASN2,ASN3',
                       help='Comma-separated list of ASNs to watch (default: 3356,6461,7018)')
    
    args = parser.parse_args()
    
    # Validate arguments
    if args.prefix and args.file:
        print("Error: Cannot specify both --prefix and --file")
        sys.exit(1)
    
    # Determine watched ASNs
    if args.watch:
        watched_asns = parse_watched_asns(args.watch)
    else:
        watched_asns = WATCHED_ASNS
    
    # Determine color usage
    use_color = not args.no_color
    
    # Get prefix(es)
    prefixes = get_prefix_input(args)
    
    # Load configuration
    servers = load_route_servers(args.config)
    if not servers:
        print("No route servers configured.")
        return
    
    # Run lookups
    try:
        batch_results = asyncio.run(run_concurrent_lookups_batch(prefixes, servers, args.debug))
        summary = summarize_batch_results(batch_results, watched_asns)
        display_batch_summary(summary, prefixes, watched_asns, args.verbose, args.detail, use_color)
        
        if args.save:
            save_batch_results(summary, prefixes, args.save, args.detail)
            
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
    except Exception as e:
        print(f"Error: {e}")
        if args.debug:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
