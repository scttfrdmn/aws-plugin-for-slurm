# MPI Support Implementation Plan

**Feature Branch:** `feature/mpi-support`
**Base Branch:** `plugin-v3`
**Target Version:** v3.1.0
**Estimated Effort:** 5-7 days
**Status:** Planning Complete, Implementation In Progress

---

## Executive Summary

The AWS Plugin for Slurm currently does not support MPI (Message Passing Interface) workloads due to asynchronous instance launching. This implementation plan adds MPI support through:

1. **Synchronous node launching** - Wait for all nodes before marking ready
2. **Placement group support** - Enable low-latency networking
3. **Health checks** - Verify nodes are operational before job starts
4. **Documentation** - Guide users on MPI configuration

The implementation is **backward compatible** - existing non-MPI workloads continue unchanged. MPI support is opt-in via configuration flags.

---

## Problem Statement

### Current Behavior (Broken for MPI)

```
Time 0s:    Slurm calls ResumeProgram with 16 nodes
Time 1s:    resume.py launches EC2 Fleet
Time 2s:    resume.py exits (instances still booting)
Time 30s:   Instance 1 boots, slurmd starts, joins Slurm
Time 35s:   Instance 2 boots, slurmd starts, joins Slurm
Time 45s:   Instance 3 boots...
Time 120s:  Instance 16 boots, slurmd starts

MPI Job:    Tries to start at Time 30s when first node ready
            Hangs waiting for other 15 nodes
            Eventually times out or fails
```

### Why This Breaks MPI

1. **Asynchronous launches** - Nodes join Slurm independently over 2+ minutes
2. **No synchronization** - MPI `mpirun` doesn't know nodes are still booting
3. **No placement groups** - Nodes scattered across racks → high latency (100μs+)
4. **No health checks** - Plugin marks nodes "ready" before slurmd is operational

### What MPI Requires

- ✅ **All nodes ready simultaneously** - Can't start until full allocation available
- ✅ **Low latency networking** - Placement groups for <10μs inter-node latency
- ✅ **Full mesh connectivity** - All nodes must reach all others (already works)
- ✅ **Synchronized start** - All MPI ranks begin together
- ⚠️ **Fast shared filesystem** - NFS over WAN is marginal for MPI-IO

---

## Architecture Changes

### New Components

```
┌─────────────────────────────────────────────────────────────────┐
│                         resume.py                                │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ 1. Detect multi-node allocation                          │  │
│  │    if nb_nodes > 1 and EnableMPISupport:                 │  │
│  └──────────────────┬───────────────────────────────────────┘  │
│                     │                                            │
│  ┌──────────────────▼───────────────────────────────────────┐  │
│  │ 2. Launch EC2 Fleet with Placement Group                 │  │
│  │    Add Placement: {GroupName: "..."}                     │  │
│  └──────────────────┬───────────────────────────────────────┘  │
│                     │                                            │
│  ┌──────────────────▼───────────────────────────────────────┐  │
│  │ 3. wait_for_instances_ready()            [NEW FUNCTION]  │  │
│  │    - Poll EC2 until all "running"                        │  │
│  │    - Call health_check.py for each node                  │  │
│  │    - Timeout after 300s (configurable)                   │  │
│  └──────────────────┬───────────────────────────────────────┘  │
│                     │                                            │
│  ┌──────────────────▼───────────────────────────────────────┐  │
│  │ 4. Update Slurm with all IPs atomically                  │  │
│  │    for node, ip in ready_nodes: scontrol update          │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                      health_check.py                   [NEW]     │
│                                                                  │
│  - check_node_health(ip, checks=['network', 'slurmd', 'nfs'])  │
│  - ping_host(ip)                                                │
│  - check_port(ip, 6818)  # slurmd                              │
│  - check_nfs_mount(ip)   # optional                            │
└─────────────────────────────────────────────────────────────────┘
```

### Configuration Schema Changes

**partitions.json** (new optional fields):

```json
{
  "NodeGroupName": "mpi",
  "MaxNodes": 64,
  "EnableMPISupport": true,              // NEW - enables synchronous launch
  "PlacementGroupName": "slurm-mpi-pg",  // NEW - for low-latency networking
  "MPIOptions": {                        // NEW - MPI-specific settings
    "WaitForAllNodes": true,
    "TimeoutSeconds": 300,
    "HealthChecks": ["network", "slurmd"],
    "RequirePlacementGroup": false
  },
  // ... existing fields (Region, LaunchTemplateSpecification, etc.)
}
```

**Backward Compatibility:** All new fields are optional. If `EnableMPISupport` is not set or false, behavior is unchanged.

---

## Implementation Phases

### Phase 1: Synchronous Node Launch (P0 - Critical)

**Goal:** Wait for all nodes in an allocation to be ready before marking available to Slurm

**Files Modified:**
- `resume.py` - Add synchronous launch mode

**New Functions in resume.py:**

```python
def wait_for_instances_ready(client, instance_ids, node_names_map, nodegroup, timeout=300):
    """
    Wait for all instances to be running and healthy

    Args:
        client: boto3 EC2 client
        instance_ids: List of instance IDs to wait for
        node_names_map: Dict mapping instance_id -> node_name
        nodegroup: Node group config (for MPIOptions)
        timeout: Max seconds to wait

    Returns:
        dict: {instance_id: {'ip': '10.1.1.50', 'hostname': 'ip-10-1-1-50'}}

    Raises:
        TimeoutError: If not all instances ready within timeout
    """
    start_time = time.time()
    ready_instances = {}
    mpi_options = nodegroup.get('MPIOptions', {})
    health_checks = mpi_options.get('HealthChecks', ['network', 'slurmd'])

    logger.info(f'Waiting for {len(instance_ids)} instances to be ready (timeout={timeout}s)')

    while time.time() - start_time < timeout:
        # Check EC2 instance states
        try:
            response = client.describe_instances(InstanceIds=instance_ids)
        except Exception as e:
            logger.warning(f'Failed to describe instances: {e}')
            time.sleep(5)
            continue

        # Build map of running instances
        for reservation in response['Reservations']:
            for instance in reservation['Instances']:
                instance_id = instance['InstanceId']
                state = instance['State']['Name']

                if state == 'running' and instance_id not in ready_instances:
                    ip_address = instance['PrivateIpAddress']
                    hostname = 'ip-%s' % '-'.join(ip_address.split('.'))

                    # Perform health checks
                    if perform_health_checks(ip_address, health_checks):
                        ready_instances[instance_id] = {
                            'ip': ip_address,
                            'hostname': hostname,
                            'node_name': node_names_map[instance_id]
                        }
                        logger.info(f'Instance {instance_id} ready: {ip_address}')

        # Check if all ready
        if len(ready_instances) == len(instance_ids):
            elapsed = time.time() - start_time
            logger.info(f'All {len(instance_ids)} instances ready after {elapsed:.1f}s')
            return ready_instances

        # Log progress
        if int(time.time() - start_time) % 30 == 0:
            logger.info(f'Progress: {len(ready_instances)}/{len(instance_ids)} instances ready')

        time.sleep(5)

    # Timeout reached
    elapsed = time.time() - start_time
    raise TimeoutError(
        f'Only {len(ready_instances)}/{len(instance_ids)} instances ready after {elapsed:.1f}s'
    )


def perform_health_checks(ip_address, checks):
    """
    Perform health checks on a node

    Args:
        ip_address: Node IP to check
        checks: List of check types ['network', 'slurmd', 'nfs']

    Returns:
        bool: True if all checks pass
    """
    if 'network' in checks:
        if not ping_host(ip_address, timeout=3):
            logger.debug(f'Health check failed for {ip_address}: cannot ping')
            return False

    if 'slurmd' in checks:
        if not check_port(ip_address, 6818, timeout=3):
            logger.debug(f'Health check failed for {ip_address}: slurmd port not responding')
            return False

    # NFS check is expensive (requires SSH), skip for now
    # Could be added later if needed

    return True


def ping_host(ip_address, timeout=3):
    """Ping a host to verify network connectivity"""
    try:
        result = subprocess.run(
            ['ping', '-c', '1', '-W', str(timeout), ip_address],
            capture_output=True,
            timeout=timeout + 1
        )
        return result.returncode == 0
    except Exception as e:
        logger.debug(f'Ping failed for {ip_address}: {e}')
        return False


def check_port(ip_address, port, timeout=3):
    """Check if a TCP port is responding"""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((ip_address, port))
        sock.close()
        return True
    except Exception as e:
        logger.debug(f'Port check failed for {ip_address}:{port}: {e}')
        return False
```

**Integration Point in resume.py** (after line 98):

```python
# Around line 98, after EC2 Fleet is created
for partition_name, nodegroups in nodes_to_resume.items():
    for nodegroup_name, node_ids in nodegroups.items():

        nb_nodes_to_resume = len(node_ids)
        nodegroup = common.get_partition_nodegroup(partition_name, nodegroup_name)

        # ... existing validation ...

        # Create and launch EC2 Fleet
        response_fleet = client.create_fleet(**request_fleet)

        # NEW: Check if MPI support is enabled
        enable_mpi = nodegroup.get('EnableMPISupport', False)
        mpi_options = nodegroup.get('MPIOptions', {})

        if enable_mpi and nb_nodes_to_resume > 1:
            logger.info(f'MPI mode: Launching {nb_nodes_to_resume} nodes synchronously')

            # Collect all instance IDs
            all_instance_ids = []
            node_names_map = {}
            node_id_index = 0

            for instance in response_fleet['Instances']:
                for instance_id in instance['InstanceIds']:
                    node_id = node_ids[node_id_index]
                    node_name = common.get_node_name(partition_name, nodegroup_name, node_id)
                    all_instance_ids.append(instance_id)
                    node_names_map[instance_id] = node_name
                    node_id_index += 1

            # Wait for all instances to be ready
            timeout = mpi_options.get('TimeoutSeconds', 300)
            try:
                ready_instances = wait_for_instances_ready(
                    client,
                    all_instance_ids,
                    node_names_map,
                    nodegroup,
                    timeout=timeout
                )
            except TimeoutError as e:
                logger.error(f'MPI node launch failed: {e}')
                logger.warning(f'Terminating {len(all_instance_ids)} instances due to timeout')
                try:
                    client.terminate_instances(InstanceIds=all_instance_ids)
                except Exception as term_error:
                    logger.error(f'Failed to terminate instances: {term_error}')
                continue

            # Tag and update Slurm for all ready instances
            for instance_id, instance_info in ready_instances.items():
                ip_address = instance_info['ip']
                hostname = instance_info['hostname']
                node_name = instance_info['node_name']

                # Tag instance (existing code)
                tags = [{'Key': 'Name', 'Value': node_name}]
                if 'Tags' in nodegroup:
                    tags += nodegroup['Tags']

                # Replace sequences
                sequences = (
                    ('{ip_address}', ip_address),
                    ('{node_name}', node_name),
                    ('{hostname}', hostname)
                )
                for tag in tags:
                    for sequence in sequences:
                        tag['Value'] = tag['Value'].replace(*sequence)

                try:
                    client.create_tags(Resources=[instance_id], Tags=tags)
                    logger.debug(f'Tagged node {node_name}')
                except Exception as e:
                    logger.error(f'Failed to tag node {node_name}: {e}')

                # Update Slurm
                try:
                    slurm_param = f'nodeaddr={ip_address} nodehostname={hostname}'
                    common.update_node(node_name, slurm_param)
                    logger.info(f'Updated Slurm for node {node_name}')
                except Exception as e:
                    logger.error(f'Failed to update Slurm for {node_name}: {e}')

        else:
            # EXISTING CODE: Asynchronous launch (non-MPI mode)
            # ... (lines 100-186 remain unchanged) ...
```

**Testing:**
- Launch 2-node allocation with `EnableMPISupport: true`
- Verify both nodes ready before `scontrol update` is called
- Submit simple MPI job: `srun -N 2 hostname`

**Estimated Effort:** 2-3 days

---

### Phase 2: Placement Group Support (P0 - Critical)

**Goal:** Enable low-latency networking for MPI jobs

**Files Modified:**
- `resume.py` - Add placement group to launch overrides
- `common.py` - Add validation for new fields

**Changes to resume.py** (in EC2 Fleet request building, around line 84):

```python
# Around line 84, when building LaunchTemplateOverrides
for override in nodegroup['LaunchTemplateOverrides']:
    for subnet in nodegroup['SubnetIds']:
        override_copy = copy.deepcopy(override)
        override_copy['SubnetId'] = subnet
        override_copy['WeightedCapacity'] = 1

        # NEW: Add placement group if specified
        if 'PlacementGroupName' in nodegroup:
            override_copy['Placement'] = {
                'GroupName': nodegroup['PlacementGroupName']
            }
            logger.debug(f'Using placement group: {nodegroup["PlacementGroupName"]}')

        request_fleet['LaunchTemplateConfigs'][0]['Overrides'].append(override_copy)
```

**Changes to common.py** (add validation, around line 112):

```python
# In validate_partitions(), add validation for new fields
for i_nodegroup, nodegroup in enumerate(partition['NodeGroups']):
    # ... existing validation ...

    # Validate MPI-specific fields
    if 'EnableMPISupport' in nodegroup:
        assert isinstance(nodegroup['EnableMPISupport'], bool), \
            f'root["Partitions"][{i_partition}]["NodeGroups"][{i_nodegroup}]["EnableMPISupport"] must be boolean'

    if 'PlacementGroupName' in nodegroup:
        assert isinstance(nodegroup['PlacementGroupName'], str), \
            f'root["Partitions"][{i_partition}]["NodeGroups"][{i_nodegroup}]["PlacementGroupName"] must be string'

    if 'MPIOptions' in nodegroup:
        assert isinstance(nodegroup['MPIOptions'], dict), \
            f'root["Partitions"][{i_partition}]["NodeGroups"][{i_nodegroup}]["MPIOptions"] must be dict'

        mpi_options = nodegroup['MPIOptions']
        if 'TimeoutSeconds' in mpi_options:
            assert isinstance(mpi_options['TimeoutSeconds'], int), \
                'MPIOptions["TimeoutSeconds"] must be integer'

        if 'HealthChecks' in mpi_options:
            assert isinstance(mpi_options['HealthChecks'], list), \
                'MPIOptions["HealthChecks"] must be array'
            valid_checks = ['network', 'slurmd', 'nfs']
            for check in mpi_options['HealthChecks']:
                assert check in valid_checks, \
                    f'MPIOptions["HealthChecks"] contains invalid check: {check}'
```

**Testing:**
- Create placement group in AWS
- Configure node group with `PlacementGroupName`
- Launch instances, verify they're in placement group:
  ```bash
  aws ec2 describe-instances --instance-ids i-xxxxx \
    --query 'Reservations[0].Instances[0].Placement.GroupName'
  ```

**Estimated Effort:** 1 day

---

### Phase 3: Health Checks (P1 - Important)

**Goal:** Verify nodes are operational before marking ready

**Files Created:**
- `health_check.py` - Standalone health check utility (future use)

**Note:** Basic health checks are already integrated in Phase 1 (`perform_health_checks()` in resume.py). This phase creates a standalone utility for:
- Manual testing
- Future enhancement (SSH-based checks)
- Monitoring scripts

**health_check.py** (new file):

```python
#!/usr/bin/python3
"""
Health check utility for Slurm cloud nodes

Usage:
    python3 health_check.py 10.1.1.50
    python3 health_check.py 10.1.1.50 --checks network,slurmd,nfs
"""

import argparse
import socket
import subprocess
import sys


def check_node_health(ip_address, checks=['network', 'slurmd']):
    """
    Perform health checks on a node

    Args:
        ip_address: Node IP to check
        checks: List of check types ['network', 'slurmd', 'nfs']

    Returns:
        (success: bool, results: dict)
    """
    results = {}

    if 'network' in checks:
        results['network'] = ping_host(ip_address)

    if 'slurmd' in checks:
        results['slurmd'] = check_port(ip_address, 6818)

    if 'nfs' in checks:
        results['nfs'] = check_nfs_mount(ip_address)

    success = all(results.values())
    return (success, results)


def ping_host(ip_address, timeout=5):
    """Ping a host to verify network connectivity"""
    try:
        result = subprocess.run(
            ['ping', '-c', '1', '-W', str(timeout), ip_address],
            capture_output=True,
            timeout=timeout + 1
        )
        return result.returncode == 0
    except Exception as e:
        return False


def check_port(ip_address, port, timeout=5):
    """Check if a TCP port is responding"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((ip_address, port))
        sock.close()
        return True
    except:
        return False


def check_nfs_mount(ip_address):
    """
    Check if NFS is mounted on the node

    Note: This requires SSH access and is not implemented yet.
    Future enhancement: SSH to node and check mount output.
    """
    # TODO: Implement SSH-based NFS check
    # For now, always return True (skip check)
    return True


def main():
    parser = argparse.ArgumentParser(description='Health check for Slurm cloud nodes')
    parser.add_argument('ip_address', help='IP address of node to check')
    parser.add_argument('--checks', default='network,slurmd',
                        help='Comma-separated list of checks (network,slurmd,nfs)')
    args = parser.parse_args()

    checks = [c.strip() for c in args.checks.split(',')]

    print(f'Running health checks on {args.ip_address}...')
    success, results = check_node_health(args.ip_address, checks)

    print('\nResults:')
    for check, result in results.items():
        status = '✓ PASS' if result else '✗ FAIL'
        print(f'  {check:12} {status}')

    if success:
        print('\n✓ All checks passed')
        sys.exit(0)
    else:
        print('\n✗ Some checks failed')
        sys.exit(1)


if __name__ == '__main__':
    main()
```

**Testing:**
```bash
# Manual test
python3 health_check.py 10.1.1.50

# Test specific checks
python3 health_check.py 10.1.1.50 --checks network,slurmd
```

**Estimated Effort:** 1-2 days (mostly testing and refinement)

---

### Phase 4: Documentation & Examples (P1 - Important)

**Goal:** Guide users on configuring and using MPI support

**Files Created:**
- `docs/mpi-support.md` - User guide
- `examples/example-5-mpi-workloads.json` - Configuration example
- Update `README.md` to mention MPI support

**Testing:**
- Follow docs/mpi-support.md from scratch
- Verify all commands work
- Submit test MPI job

**Estimated Effort:** 1 day

---

## Configuration Examples

### Example 1: Basic MPI Support (Single Instance Type)

**examples/example-5-mpi-workloads.json:**

```json
{
  "Partitions": [
    {
      "PartitionName": "mpi",
      "NodeGroups": [
        {
          "NodeGroupName": "compute",
          "MaxNodes": 64,
          "Region": "us-east-1",
          "EnableMPISupport": true,
          "PlacementGroupName": "slurm-mpi-pg",
          "MPIOptions": {
            "WaitForAllNodes": true,
            "TimeoutSeconds": 300,
            "HealthChecks": ["network", "slurmd"]
          },
          "SlurmSpecifications": {
            "CPUs": "96",
            "RealMemory": "190000",
            "Feature": "lowlatency",
            "Weight": "1"
          },
          "PurchasingOption": "on-demand",
          "OnDemandOptions": {
            "AllocationStrategy": "lowest-price"
          },
          "LaunchTemplateSpecification": {
            "LaunchTemplateName": "mpi-compute-template",
            "Version": "$Latest"
          },
          "LaunchTemplateOverrides": [
            {
              "InstanceType": "c5n.18xlarge"
            }
          ],
          "SubnetIds": [
            "subnet-11111111"
          ],
          "Tags": [
            {
              "Key": "Workload",
              "Value": "MPI"
            },
            {
              "Key": "NodeGroup",
              "Value": "mpi-compute"
            }
          ]
        }
      ],
      "PartitionOptions": {
        "Default": "no",
        "OverSubscribe": "NO"
      }
    }
  ]
}
```

### Example 2: Mixed CPU + MPI Partitions

```json
{
  "Partitions": [
    {
      "PartitionName": "cpu",
      "NodeGroups": [
        {
          "NodeGroupName": "general",
          "MaxNodes": 100,
          "Region": "us-east-1",
          "SlurmSpecifications": {
            "CPUs": "8",
            "RealMemory": "16000"
          },
          "PurchasingOption": "spot",
          "SpotOptions": {
            "AllocationStrategy": "price-capacity-optimized"
          },
          "LaunchTemplateSpecification": {
            "LaunchTemplateName": "cpu-template",
            "Version": "$Latest"
          },
          "LaunchTemplateOverrides": [
            {
              "InstanceType": "c5.2xlarge"
            }
          ],
          "SubnetIds": [
            "subnet-11111111",
            "subnet-22222222"
          ]
        }
      ],
      "PartitionOptions": {
        "Default": "yes"
      }
    },
    {
      "PartitionName": "mpi",
      "NodeGroups": [
        {
          "NodeGroupName": "hpc",
          "MaxNodes": 32,
          "Region": "us-east-1",
          "EnableMPISupport": true,
          "PlacementGroupName": "slurm-mpi-hpc",
          "MPIOptions": {
            "WaitForAllNodes": true,
            "TimeoutSeconds": 300,
            "HealthChecks": ["network", "slurmd"]
          },
          "SlurmSpecifications": {
            "CPUs": "96",
            "RealMemory": "190000",
            "Feature": "lowlatency"
          },
          "PurchasingOption": "on-demand",
          "LaunchTemplateSpecification": {
            "LaunchTemplateName": "mpi-template",
            "Version": "$Latest"
          },
          "LaunchTemplateOverrides": [
            {
              "InstanceType": "c5n.18xlarge"
            }
          ],
          "SubnetIds": [
            "subnet-11111111"
          ]
        }
      ],
      "PartitionOptions": {
        "Default": "no",
        "OverSubscribe": "NO"
      }
    }
  ]
}
```

---

## Testing Plan

### Unit Tests

**Test 1: Synchronous Launch with 2 Nodes**
```bash
# Configure partition with EnableMPISupport: true, MaxNodes: 2
srun -p mpi -N 2 hostname
# Verify: Both nodes ready before job starts
# Expected: Logs show "Waiting for 2 instances to be ready..."
```

**Test 2: Synchronous Launch with 8 Nodes**
```bash
srun -p mpi -N 8 hostname
# Verify: All 8 nodes ready simultaneously
# Expected: Job starts only after all nodes operational
```

**Test 3: Timeout Behavior**
```bash
# Temporarily set MPIOptions.TimeoutSeconds: 10
srun -p mpi -N 4 hostname
# Verify: If nodes don't launch within 10s, job fails gracefully
# Expected: Instances terminated, error logged
```

**Test 4: Placement Group Assignment**
```bash
srun -p mpi -N 4 hostname
# Check placement group on launched instances
aws ec2 describe-instances --instance-ids i-xxxxx \
  --query 'Reservations[0].Instances[0].Placement.GroupName'
# Expected: "slurm-mpi-pg"
```

**Test 5: Health Check Failures**
```bash
# Block slurmd port 6818 on one node (firewall rule)
srun -p mpi -N 2 hostname
# Verify: Job fails, unhealthy node not added to Slurm
# Expected: Error in /var/log/slurm/aws_plugin.log
```

**Test 6: Backward Compatibility**
```bash
# Run job on non-MPI partition (EnableMPISupport: false)
srun -p cpu -N 4 hostname
# Verify: Existing behavior unchanged (asynchronous launch)
# Expected: Job runs normally
```

### Integration Tests

**Test 7: Simple MPI Job**
```bash
# Create test_mpi.c
cat > test_mpi.c <<'EOF'
#include <mpi.h>
#include <stdio.h>

int main(int argc, char** argv) {
    MPI_Init(&argc, &argv);
    int world_rank, world_size;
    MPI_Comm_rank(MPI_COMM_WORLD, &world_rank);
    MPI_Comm_size(MPI_COMM_WORLD, &world_size);
    printf("Hello from rank %d of %d\n", world_rank, world_size);
    MPI_Finalize();
    return 0;
}
EOF

mpicc -o test_mpi test_mpi.c
srun -p mpi -N 4 -n 16 ./test_mpi
# Expected: 16 "Hello from rank X of 16" messages
```

**Test 8: MPI Latency Benchmark (OSU Micro-Benchmarks)**
```bash
# Install OSU Micro-Benchmarks on AMI
wget http://mvapich.cse.ohio-state.edu/download/mvapich/osu-micro-benchmarks-5.9.tar.gz
tar xzf osu-micro-benchmarks-5.9.tar.gz
cd osu-micro-benchmarks-5.9
./configure CC=mpicc CXX=mpicxx
make && make install

# Run latency test
srun -p mpi -N 2 -n 2 /usr/local/libexec/osu-micro-benchmarks/mpi/pt2pt/osu_latency

# Expected with placement group: <10μs latency
# Expected without placement group: 50-200μs latency
```

**Test 9: Mixed Workload (MPI + CPU)**
```bash
# Submit CPU job
sbatch -p cpu -N 1 cpu_job.sh

# Submit MPI job while CPU job running
sbatch -p mpi -N 8 mpi_job.sh

# Verify: Both run independently without interference
```

### Performance Tests

**Test 10: Launch Time Measurement**
```bash
# Measure time from srun submission to job start
for nodes in 2 4 8 16; do
  echo "Testing $nodes nodes..."
  time srun -p mpi -N $nodes hostname
done

# Expected launch times:
# 2 nodes:  60-90 seconds
# 4 nodes:  70-100 seconds
# 8 nodes:  80-120 seconds
# 16 nodes: 90-150 seconds
```

**Test 11: MPI Performance (IMB)**
```bash
# Intel MPI Benchmarks
srun -p mpi -N 4 -n 16 IMB-MPI1 PingPong

# Verify: Performance matches instance specs
# c5n.18xlarge should show ~100 Gbps bandwidth
```

---

## Known Limitations

### Hard Limits

1. **Placement Group Capacity**
   - Max instances varies by instance type
   - c5n.18xlarge: ~20-30 instances per placement group
   - Workaround: Use multiple placement groups (requires code changes)

2. **Single AZ Requirement**
   - Placement groups don't span availability zones
   - Configuration: Must use single subnet in `SubnetIds`

3. **Launch Timeout**
   - Default: 300 seconds (5 minutes)
   - If instances don't boot in time, job fails
   - Configurable via `MPIOptions.TimeoutSeconds`

4. **EC2 Fleet Limitations**
   - EC2 may not honor placement group if capacity constrained
   - No way to force placement group (AWS limitation)
   - Workaround: Use RunInstances API (future enhancement)

### Soft Limits

5. **NFS Over WAN Performance**
   - NFS from on-prem → AWS adds latency to file I/O
   - MPI-IO intensive workloads may suffer
   - Workaround: Use EFS or FSx for Lustre in AWS

6. **Boot Time Variability**
   - Instance boot time: 30-120 seconds (depends on AMI size)
   - Large allocations may hit timeout on slow boots
   - Mitigation: Optimize AMI size, increase timeout

7. **No Dynamic Placement Groups**
   - Must pre-create placement group in AWS
   - Cannot create per-job placement groups
   - Future: Auto-create/destroy placement groups

8. **Spot Instance Risk**
   - Spot interruptions mid-MPI job are catastrophic
   - Recommendation: Use on-demand for MPI partition
   - Future: Add spot interruption handling

---

## Rollout Plan

### Development (Week 1)

**Days 1-3: Phase 1 Implementation**
- Implement `wait_for_instances_ready()`
- Add health check functions
- Integrate synchronous launch mode
- Unit test with 2-node allocation

**Days 4-5: Phase 2 Implementation**
- Add placement group support
- Update validation in common.py
- Test placement group assignment

### Testing (Week 2)

**Days 6-7: Phase 3 & Integration Testing**
- Create health_check.py utility
- Run all unit tests (Tests 1-6)
- Run integration tests (Tests 7-9)
- Fix bugs

**Day 8: Documentation**
- Write docs/mpi-support.md
- Create examples/example-5-mpi-workloads.json
- Update README.md

### Review & Merge (Week 2-3)

**Days 9-10: Performance Testing & Refinement**
- Run performance tests (Tests 10-11)
- Tune timeouts and health check intervals
- Load testing with larger allocations

**Day 11: Code Review & Merge**
- Create pull request: `feature/mpi-support` → `plugin-v3`
- Address review comments
- Merge to main branch

---

## Success Criteria

### Functional Requirements

- ✅ MPI jobs with 2-64 nodes launch successfully
- ✅ All nodes ready before job starts (no hangs)
- ✅ Placement group correctly assigned
- ✅ Health checks prevent unhealthy nodes from joining
- ✅ Timeout handling terminates failed launches gracefully
- ✅ Backward compatible with existing non-MPI workloads

### Performance Requirements

- ✅ Launch time: <150 seconds for 16 nodes
- ✅ Inter-node latency: <10μs with placement group
- ✅ Simple MPI job (hostname) completes successfully
- ✅ OSU Micro-Benchmarks show expected latency/bandwidth

### Documentation Requirements

- ✅ Complete user guide (docs/mpi-support.md)
- ✅ Working example configuration
- ✅ Troubleshooting section with common issues
- ✅ README.md mentions MPI support

---

## Risk Mitigation

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| EC2 Fleet ignores placement group | High | Low | Document limitation, consider RunInstances API |
| Long boot times cause timeouts | Medium | Medium | Make timeout configurable, optimize AMI |
| Health checks too strict (false negatives) | Medium | Medium | Make checks configurable, add retry logic |
| Backward compatibility break | High | Low | Extensive testing of non-MPI partitions |
| NFS latency kills MPI-IO performance | Medium | High | Document EFS/FSx alternatives clearly |
| Users forget to create placement group | Low | High | Add validation, fail early with clear error |

---

## Future Enhancements (Post-v3.1.0)

### Phase 5: Advanced Features (Future)

1. **EFA (Elastic Fabric Adapter) Support**
   - Auto-detect EFA-capable instances
   - Configure EFA in launch template
   - Add EFA health checks

2. **Dynamic Placement Groups**
   - Create placement group per job
   - Clean up after job completes
   - Handle multi-job contention

3. **Multi-Tier Launch**
   - Launch "head" node first
   - Wait for head to be ready
   - Launch workers pointing to head

4. **Enhanced Monitoring**
   - CloudWatch metrics for launch times
   - MPI job performance metrics
   - Alert on repeated failures

5. **MPI Library Management**
   - Detect MPI library versions
   - Validate compatibility across nodes
   - Auto-install if needed

6. **Spot Instance Support for MPI**
   - Checkpoint/restart support
   - Spot interruption handling
   - Migrate to on-demand on interruption

---

## Appendix A: File Inventory

### New Files
- `docs/IMPLEMENTATION_PLAN_MPI.md` - This document
- `docs/mpi-support.md` - User guide for MPI support
- `examples/example-5-mpi-workloads.json` - MPI configuration example
- `health_check.py` - Standalone health check utility

### Modified Files
- `resume.py` - Synchronous launch, placement groups, health checks
- `common.py` - Validation for new config fields
- `README.md` - Mention MPI support

### Documentation Updates
- `docs/troubleshooting.md` - Add MPI-specific troubleshooting
- `docs/advanced-usage.md` - Link to MPI guide
- `docs/performance-tuning.md` - MPI performance tips

---

## Appendix B: Configuration Reference

### New partitions.json Fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `EnableMPISupport` | boolean | No | false | Enable synchronous launch for MPI |
| `PlacementGroupName` | string | No | null | AWS placement group name |
| `MPIOptions` | object | No | {} | MPI-specific settings |
| `MPIOptions.WaitForAllNodes` | boolean | No | true | Wait for all nodes before marking ready |
| `MPIOptions.TimeoutSeconds` | integer | No | 300 | Max seconds to wait for nodes |
| `MPIOptions.HealthChecks` | array | No | ["network", "slurmd"] | List of health checks to perform |
| `MPIOptions.RequirePlacementGroup` | boolean | No | false | Fail if placement group unavailable |

### Example Minimal MPI Configuration

```json
{
  "NodeGroupName": "mpi",
  "MaxNodes": 16,
  "Region": "us-east-1",
  "EnableMPISupport": true,
  "SlurmSpecifications": {
    "CPUs": "96"
  },
  "PurchasingOption": "on-demand",
  "LaunchTemplateSpecification": {
    "LaunchTemplateName": "mpi-template",
    "Version": "$Latest"
  },
  "LaunchTemplateOverrides": [
    {"InstanceType": "c5n.18xlarge"}
  ],
  "SubnetIds": ["subnet-xxxxx"]
}
```

---

## Appendix C: Error Messages

### New Error Messages Added

**Error 1: MPI launch timeout**
```
ERROR - MPI node launch failed: Only 6/16 instances ready after 300.0s
WARNING - Terminating 16 instances due to timeout
```

**Resolution:** Increase `MPIOptions.TimeoutSeconds` or optimize AMI boot time

**Error 2: Placement group capacity**
```
EC2 Fleet error - InsufficientInstanceCapacity - Instance type: c5n.18xlarge Placement group: slurm-mpi-pg
```

**Resolution:** Placement group full or unavailable. Try different instance type or remove placement group temporarily.

**Error 3: Health check failure**
```
Health check failed for 10.1.1.50: slurmd port not responding
```

**Resolution:** slurmd not starting properly. Check AMI configuration and /var/log/slurmd.log on instance.

**Error 4: Placement group not found**
```
EC2 Fleet error - InvalidPlacementGroup.Unknown - Placement group 'slurm-mpi-pg' does not exist
```

**Resolution:** Create placement group:
```bash
aws ec2 create-placement-group --group-name slurm-mpi-pg --strategy cluster
```

---

**End of Implementation Plan**

Last Updated: 2025-09-30
Author: Claude Code
Status: Ready for Implementation
