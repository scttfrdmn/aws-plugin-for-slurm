#!/usr/bin/python3
"""
Health check utility for Slurm cloud nodes

This utility performs health checks on cloud compute nodes to verify they are
operational before being added to the Slurm cluster.

Usage:
    python3 health_check.py 10.1.1.50
    python3 health_check.py 10.1.1.50 --checks network,slurmd
    python3 health_check.py 10.1.1.50 --checks network --timeout 10

Checks:
    network - Ping host to verify network connectivity (requires ICMP to be allowed)
    slurmd  - Check if slurmd port (6818) is responding
"""

import argparse
import socket
import subprocess
import sys


SLURMD_PORT = 6818

VALID_CHECKS = ('network', 'slurmd')


def check_node_health(ip_address, checks=('network', 'slurmd'), timeout=5):
    """
    Perform health checks on a node

    Args:
        ip_address: Node IP to check
        checks: List of check types ['network', 'slurmd']
        timeout: Timeout in seconds for each check

    Returns:
        (success: bool, results: dict)
    """
    results = {}

    if 'network' in checks:
        results['network'] = ping_host(ip_address, timeout=timeout)

    if 'slurmd' in checks:
        results['slurmd'] = check_port(ip_address, SLURMD_PORT, timeout=timeout)

    success = all(results.values())
    return (success, results)


def ping_host(ip_address, timeout=5):
    """
    Ping a host to verify network connectivity

    Args:
        ip_address: IP address to ping
        timeout: Timeout in seconds

    Returns:
        bool: True if ping successful
    """
    try:
        result = subprocess.run(
            ['ping', '-c', '1', '-W', str(timeout), ip_address],
            capture_output=True,
            timeout=timeout + 1
        )
        return result.returncode == 0
    except Exception as e:
        print(f'  Error: {e}', file=sys.stderr)
        return False


def check_port(ip_address, port, timeout=5):
    """
    Check if a TCP port is responding

    Args:
        ip_address: IP address to check
        port: TCP port number
        timeout: Timeout in seconds

    Returns:
        bool: True if port is responding
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((ip_address, port))
        return True
    except Exception as e:
        print(f'  Error: {e}', file=sys.stderr)
        return False
    finally:
        sock.close()


def main():
    parser = argparse.ArgumentParser(
        description='Health check utility for Slurm cloud nodes',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Check network and slurmd (default)
  %(prog)s 10.1.1.50

  # Check only network connectivity
  %(prog)s 10.1.1.50 --checks network

  # Check both with custom timeout
  %(prog)s 10.1.1.50 --checks network,slurmd --timeout 10

  # Batch check multiple nodes
  for ip in 10.1.1.{50..53}; do %(prog)s $ip; done
        """
    )
    parser.add_argument('ip_address', help='IP address of node to check')
    parser.add_argument('--checks', default='network,slurmd',
                        help='Comma-separated list of checks: network,slurmd (default: network,slurmd)')
    parser.add_argument('--timeout', type=int, default=5,
                        help='Timeout in seconds for each check (default: 5)')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Verbose output (show details for passed checks)')
    args = parser.parse_args()

    checks = [c.strip() for c in args.checks.split(',')]

    # Validate check names
    for check in checks:
        if check not in VALID_CHECKS:
            print(f'Error: Invalid check "{check}". Valid checks: {", ".join(VALID_CHECKS)}',
                  file=sys.stderr)
            sys.exit(2)

    print(f'Running health checks on {args.ip_address}...')
    if args.verbose:
        print(f'  Checks: {", ".join(checks)}')
        print(f'  Timeout: {args.timeout}s')
        print()

    success, results = check_node_health(args.ip_address, checks, timeout=args.timeout)

    print('\nResults:')
    for check, result in results.items():
        if result:
            status = '\u2713 PASS'
            color_code = '\033[92m'  # Green
        else:
            status = '\u2717 FAIL'
            color_code = '\033[91m'  # Red

        reset_code = '\033[0m'
        print(f'  {check:12} {color_code}{status}{reset_code}')

    print()
    if success:
        print('\u2713 All checks passed')
        sys.exit(0)
    else:
        print('\u2717 Some checks failed')
        sys.exit(1)


if __name__ == '__main__':
    main()
