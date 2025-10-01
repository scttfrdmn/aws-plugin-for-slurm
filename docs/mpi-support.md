# MPI Support

**Added in:** v3.1.0
**Status:** Stable

## Overview

The AWS Plugin for Slurm now supports MPI (Message Passing Interface) workloads through **synchronous node launching**. This ensures all nodes in an MPI allocation are ready simultaneously before the job starts, preventing hangs and failures.

### What's New

- ✅ **Synchronous launch mode** - Wait for all nodes before job starts
- ✅ **Placement group support** - Low-latency networking (<10μs)
- ✅ **Health checks** - Verify nodes are operational before adding to Slurm
- ✅ **Configurable timeouts** - Graceful failure handling
- ✅ **Backward compatible** - Non-MPI workloads unchanged

### Key Benefits

| Feature | Benefit |
|---------|---------|
| **Synchronous Launch** | All MPI ranks start together - no more hangs |
| **Placement Groups** | <10μs latency vs 50-200μs without |
| **Health Checks** | Catch boot failures before job attempts to run |
| **Fast Failure** | Timeout and cleanup failed launches automatically |

---

## Prerequisites

Before using MPI support, ensure you have:

### 1. Placement Group (Recommended)

For optimal MPI performance:

```bash
aws ec2 create-placement-group \
  --group-name slurm-mpi-pg \
  --strategy cluster \
  --region us-east-1
```

**Note:** Placement groups are AZ-specific. All nodes must launch in the same availability zone.

### 2. Single-AZ Subnet Configuration

In `partitions.json`, use only ONE subnet for MPI node groups:

```json
"SubnetIds": [
  "subnet-11111111"  // Must be single subnet for placement group
]
```

### 3. MPI-Enabled AMI

Your AMI must have:
- MPI library installed (OpenMPI, Intel MPI, MPICH, etc.)
- Same MPI version across all nodes
- slurmd configured to start on boot

**Tip:** Use the [Packer template](../examples/packer/) to build consistent AMIs.

### 4. On-Demand Instances (Recommended)

Spot interruptions mid-MPI job are catastrophic:

```json
"PurchasingOption": "on-demand"
```

**Reason:** Spot instance can be interrupted at any time, killing your entire MPI job.

---

## Configuration

### Basic MPI Configuration

Minimal configuration in `partitions.json`:

```json
{
  "NodeGroupName": "mpi",
  "MaxNodes": 32,
  "Region": "us-east-1",
  "EnableMPISupport": true,  // Enable synchronous launch
  "SlurmSpecifications": {
    "CPUs": "96",
    "RealMemory": "190000"
  },
  "PurchasingOption": "on-demand",
  "LaunchTemplateSpecification": {
    "LaunchTemplateName": "mpi-template",
    "Version": "$Latest"
  },
  "LaunchTemplateOverrides": [
    {"InstanceType": "c7gn.16xlarge"}
  ],
  "SubnetIds": ["subnet-xxxxx"]
}
```

### Full MPI Configuration

Complete configuration with all options:

```json
{
  "NodeGroupName": "mpi",
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
    {"InstanceType": "c7gn.16xlarge"},
    {"InstanceType": "c6in.32xlarge"}
  ],

  "SubnetIds": [
    "subnet-11111111"
  ]
}
```

### Configuration Reference

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `EnableMPISupport` | boolean | **Yes** | false | Enable synchronous launch mode |
| `PlacementGroupName` | string | No | null | AWS placement group for low latency |
| `MPIOptions` | object | No | {} | MPI-specific settings |
| `MPIOptions.WaitForAllNodes` | boolean | No | true | Wait for all nodes before marking ready |
| `MPIOptions.TimeoutSeconds` | integer | No | 300 | Max seconds to wait for nodes (1-600) |
| `MPIOptions.HealthChecks` | array | No | ["network", "slurmd"] | Health checks to perform |

**Health Check Types:**
- `network` - Ping node to verify connectivity
- `slurmd` - Check if slurmd port (6818) is responding
- `nfs` - Verify NFS mount (not yet implemented)

---

## Slurm Configuration

### 1. Configure Partition

In `slurm.conf`, create an MPI partition with `OverSubscribe=NO`:

```bash
# MPI partition - no oversubscription
PartitionName=mpi Nodes=mpi-compute-[0-63] Default=NO OverSubscribe=NO State=UP
```

**Important:** `OverSubscribe=NO` prevents multiple jobs from sharing nodes, which breaks MPI.

### 2. Configure Features (Optional)

Tag nodes with features for job targeting:

```bash
# In SlurmSpecifications in partitions.json
"Feature": "lowlatency,mpi"
```

Then in `slurm.conf`:

```bash
NodeName=mpi-compute-[0-63] State=CLOUD CPUs=96 Feature=lowlatency,mpi
```

---

## Usage

### Submit MPI Job with srun

```bash
# Simple hostname test
srun -p mpi -N 4 hostname

# MPI job with 4 nodes, 16 processes per node (64 total)
srun -p mpi -N 4 -n 64 ./mpi_application

# Request specific features
srun -p mpi -C lowlatency -N 8 -n 256 ./mpi_application
```

### Submit MPI Job with sbatch

Create `mpi_job.sh`:

```bash
#!/bin/bash
#SBATCH --partition=mpi
#SBATCH --nodes=4
#SBATCH --ntasks=64
#SBATCH --time=01:00:00
#SBATCH --job-name=mpi_test

# Load MPI module if using modules
# module load openmpi

# Run MPI application
srun ./mpi_application
```

Submit:

```bash
sbatch mpi_job.sh
```

### Monitor Launch Progress

Watch plugin logs during node launch:

```bash
tail -f /var/log/slurm/aws_plugin.log
```

Expected output:

```
INFO - MPI mode enabled: launching 4 nodes synchronously
INFO - MPI: Waiting for 4 instances to be ready (timeout=300s, checks=network,slurmd)
INFO - MPI: Instance i-xxxxx1 ready (10.1.1.50) [1/4]
INFO - MPI: Instance i-xxxxx2 ready (10.1.1.51) [2/4]
INFO - MPI: Instance i-xxxxx3 ready (10.1.1.52) [3/4]
INFO - MPI: Instance i-xxxxx4 ready (10.1.1.53) [4/4]
INFO - MPI: All 4 instances ready after 87.3s
INFO - MPI: All 4 nodes configured and ready
```

---

## Verification

### Check Placement Group Assignment

After launching nodes, verify they're in the placement group:

```bash
# Get instance IDs
squeue -o "%N %i" | grep mpi

# Check placement
aws ec2 describe-instances \
  --instance-ids i-xxxxx \
  --query 'Reservations[0].Instances[0].Placement' \
  --output json
```

Expected output:

```json
{
    "AvailabilityZone": "us-east-1a",
    "GroupName": "slurm-mpi-pg",
    "Tenancy": "default"
}
```

### Test MPI Communication

Simple MPI test program:

```c
// test_mpi.c
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
```

Compile and run:

```bash
mpicc -o test_mpi test_mpi.c
srun -p mpi -N 2 -n 8 ./test_mpi
```

Expected output:

```
Hello from rank 0 of 8
Hello from rank 1 of 8
Hello from rank 2 of 8
...
Hello from rank 7 of 8
```

### Measure MPI Latency

Use OSU Micro-Benchmarks:

```bash
# Install OSU Micro-Benchmarks (on AMI)
wget http://mvapich.cse.ohio-state.edu/download/mvapich/osu-micro-benchmarks-5.9.tar.gz
tar xzf osu-micro-benchmarks-5.9.tar.gz
cd osu-micro-benchmarks-5.9
./configure CC=mpicc CXX=mpicxx
make && make install

# Run latency test
srun -p mpi -N 2 -n 2 /usr/local/libexec/osu-micro-benchmarks/mpi/pt2pt/osu_latency
```

Expected results **with placement group**:

```
# OSU MPI Latency Test
# Size          Latency (us)
0                       2.45
1                       2.48
2                       2.51
4                       2.55
8                       2.63
...
```

Expected results **without placement group**: 50-200μs (20-80x worse!)

---

## Troubleshooting

### Nodes Stuck in `alloc#` State

**Symptom:** Nodes never transition to `alloc`, job hangs

**Diagnosis:**

```bash
# Check plugin log
tail -50 /var/log/slurm/aws_plugin.log

# Look for:
# - "MPI node launch failed: Only X/Y instances ready after 300.0s"
# - Health check failures
# - EC2 Fleet errors
```

**Solutions:**

1. **Timeout too short**: Increase `MPIOptions.TimeoutSeconds` to 600
2. **Instance boot slow**: Optimize AMI size, use faster instance types
3. **Health checks too strict**: Remove `slurmd` check temporarily:
   ```json
   "HealthChecks": ["network"]
   ```

### High Latency Between Nodes

**Symptom:** MPI job runs but very slow, latency >50μs

**Diagnosis:**

```bash
# Verify placement group
aws ec2 describe-instances \
  --filters "Name=tag:NodeGroup,Values=mpi-compute" \
  --query 'Reservations[].Instances[].[InstanceId,Placement.GroupName]' \
  --output table
```

**Solutions:**

1. **Placement group not assigned**: Check `PlacementGroupName` in config
2. **Multiple AZs**: Ensure only single subnet in `SubnetIds`
3. **Placement group full**: EC2 capacity limit, try different instance type

### Job Fails at MPI_Init

**Symptom:** Nodes join Slurm but MPI job crashes at `MPI_Init()`

**Diagnosis:**

```bash
# From a node in the allocation
ssh mpi-compute-0
ping mpi-compute-1  # Test connectivity
nc -zv mpi-compute-1 22  # Test SSH port
```

**Solutions:**

1. **Security group blocking**: Allow all traffic within security group:
   ```bash
   aws ec2 authorize-security-group-ingress \
     --group-id sg-compute \
     --source-group sg-compute \
     --protocol all
   ```

2. **MPI version mismatch**: Verify same MPI library on all nodes:
   ```bash
   srun -p mpi -N 4 mpirun --version
   ```

3. **Hostname resolution**: Check `/etc/hosts` or DNS

### Placement Group Capacity Error

**Symptom:** `EC2 Fleet error - InsufficientInstanceCapacity - Placement group`

**Explanation:** Placement groups have limits (typically 20-40 instances for c7gn/c6in types)

**Solutions:**

1. **Try different instance type**: Some types have higher limits
2. **Reduce allocation size**: Request fewer nodes
3. **Use multiple placement groups**: Advanced - requires code changes
4. **Remove placement group temporarily**: Jobs will work but with higher latency

### Timeout During Launch

**Symptom:** `MPI node launch failed: Only 6/16 instances ready after 300.0s`

**Diagnosis:**

```bash
# Check EC2 Fleet errors in log
grep "EC2 Fleet error" /var/log/slurm/aws_plugin.log

# Check AWS capacity
aws ec2 describe-instance-type-offerings \
  --location-type availability-zone \
  --filters Name=instance-type,Values=c7gn.16xlarge \
  --region us-east-1
```

**Solutions:**

1. **Increase timeout**: Set `TimeoutSeconds: 600`
2. **Add instance type flexibility**:
   ```json
   "LaunchTemplateOverrides": [
     {"InstanceType": "c7gn.16xlarge"},
     {"InstanceType": "c6in.32xlarge"},
     {"InstanceType": "c7i.48xlarge"}
   ]
   ```
3. **Try different region/AZ**: Capacity varies

---

## Performance Tips

### 1. Choose Right Instance Type

| Instance Type | vCPUs | Network | MPI Suitability | Notes |
|---------------|-------|---------|-----------------|-------|
| **c7gn.16xlarge** | 64 | 200 Gbps | ⭐⭐⭐⭐⭐ | Latest gen, ARM Graviton3, best network |
| **c7i.48xlarge** | 192 | 200 Gbps | ⭐⭐⭐⭐⭐ | Latest gen x86, huge core count |
| **c6in.32xlarge** | 128 | 200 Gbps | ⭐⭐⭐⭐⭐ | Network optimized, excellent for MPI |
| **m7i.48xlarge** | 192 | 100 Gbps | ⭐⭐⭐⭐ | High memory + good network |
| **c6i.32xlarge** | 128 | 50 Gbps | ⭐⭐⭐ | Previous gen, still solid |

**Recommendation:** Use 7th generation (c7*, m7*) or 6th gen with "n" suffix (c6in) for best MPI performance.

### 2. Optimize NFS Performance

NFS from on-prem → AWS adds latency to MPI-IO:

**Solutions:**

| Option | Latency | Throughput | Cost | Best For |
|--------|---------|------------|------|----------|
| **On-prem NFS** | High (50-100ms) | Low (<100 MB/s) | $0 | Read-mostly, small I/O |
| **Amazon EFS** | Low (<5ms) | Medium (1-3 GB/s) | $$$ | General MPI-IO |
| **FSx for Lustre** | Very Low (<1ms) | Very High (10-100 GB/s) | $$$$ | Heavy MPI-IO |
| **Local NVMe** | Lowest (<0.1ms) | Highest (>GB/s) | Included | Scratch, no persistence |

**Recommendation:** For MPI-IO heavy workloads, use FSx for Lustre or local NVMe.

### 3. Tune MPI Parameters

**For OpenMPI:**

```bash
#!/bin/bash
#SBATCH --partition=mpi
#SBATCH --nodes=8

# Optimize for low-latency network
export OMPI_MCA_btl="^openib"  # Don't use InfiniBand (not available)
export OMPI_MCA_btl_tcp_if_include="eth0"  # Use primary ethernet
export OMPI_MCA_pml="ob1"  # Use optimized point-to-point

srun ./mpi_application
```

**For Intel MPI:**

```bash
export I_MPI_FABRICS=shm:tcp  # Shared memory + TCP
export I_MPI_TCP_NETMASK=eth0
```

### 4. Use Process Pinning

Pin MPI ranks to CPU cores for better cache locality:

```bash
# OpenMPI
srun --cpu-bind=cores ./mpi_application

# Intel MPI
export I_MPI_PIN=1
export I_MPI_PIN_DOMAIN=core
```

---

## Limitations

### Hard Limits

1. **Placement Group Capacity**
   - Varies by instance type (typically 20-60 instances)
   - c7gn.16xlarge: ~40 instances max
   - c6in.32xlarge: ~35 instances max
   - Cannot span availability zones

2. **Single AZ Requirement**
   - All nodes must be in same AZ (placement group limitation)
   - Reduces fault tolerance

3. **Launch Timeout**
   - Default 300s (5 minutes)
   - If instances don't boot in time, launch fails
   - Configurable up to 600s (10 minutes)

4. **Max Nodes Per Allocation**
   - Practical limit: 64 nodes
   - Theoretical limit: MaxNodes in config

### Soft Limits

5. **NFS Over WAN**
   - High latency for MPI-IO (50-100ms)
   - Consider EFS or FSx for Lustre

6. **Spot Instance Risk**
   - Spot interruption = job failure
   - Use on-demand for production MPI

7. **No EFA Auto-Configuration**
   - Elastic Fabric Adapter must be configured in launch template manually
   - Future: Auto-detect EFA capability

8. **Single-Job Launch**
   - Plugin launches one allocation at a time
   - Multiple simultaneous MPI jobs may race for placement group capacity

---

## Best Practices

### 1. Always Use Placement Groups

**Why:** 20-80x better latency (2μs vs 50-200μs)

```bash
# Create placement group first
aws ec2 create-placement-group \
  --group-name slurm-mpi-pg \
  --strategy cluster
```

### 2. Use On-Demand Instances

**Why:** Spot interruptions mid-MPI job are catastrophic

```json
"PurchasingOption": "on-demand"
```

### 3. Test with Small Allocations First

**Why:** Debug configuration before large expensive launches

```bash
# Start with 2 nodes
srun -p mpi -N 2 hostname

# Then 4 nodes
srun -p mpi -N 4 ./test_mpi

# Finally full allocation
srun -p mpi -N 64 ./production_mpi_job
```

### 4. Monitor Launch Times

**Why:** Detect capacity issues early

```bash
# Time the launch
time srun -p mpi -N 8 hostname

# Expected: 60-120 seconds
# If >180 seconds: investigate
```

### 5. Set Reasonable Timeout

**Why:** Balance between patience and fast failure

```json
"MPIOptions": {
  "TimeoutSeconds": 300  // 5 minutes for most workloads
}
```

Increase for large allocations (16+ nodes):

```json
"TimeoutSeconds": 600  // 10 minutes for 32-64 nodes
```

### 6. Use Instance Type Flexibility

**Why:** Improve chances of capacity availability

```json
"LaunchTemplateOverrides": [
  {"InstanceType": "c7gn.16xlarge"},
  {"InstanceType": "c6in.32xlarge"},
  {"InstanceType": "c7i.48xlarge"}
]
```

**Caution:** Ensure all types have similar specs for predictable performance.

---

## Examples

### Example 1: Simple MPI Test

```bash
# Create test program
cat > hello_mpi.c <<'EOF'
#include <mpi.h>
#include <stdio.h>

int main(int argc, char** argv) {
    MPI_Init(&argc, &argv);
    int rank, size;
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &size);
    printf("Hello from rank %d of %d\n", rank, size);
    MPI_Finalize();
    return 0;
}
EOF

# Compile
mpicc -o hello_mpi hello_mpi.c

# Run on 4 nodes, 16 processes
srun -p mpi -N 4 -n 16 ./hello_mpi
```

### Example 2: MPI with SBATCH

```bash
#!/bin/bash
#SBATCH --partition=mpi
#SBATCH --nodes=8
#SBATCH --ntasks-per-node=96
#SBATCH --time=02:00:00
#SBATCH --job-name=cfd_simulation
#SBATCH --output=cfd_%j.out
#SBATCH --error=cfd_%j.err

# Load environment
module load openmpi/4.1.1

# Run simulation
srun ./cfd_solver input.dat
```

### Example 3: Check Health Before Job

```bash
# Launch nodes
srun -p mpi -N 4 hostname

# In another terminal, check health
python3 health_check.py 10.1.1.50
python3 health_check.py 10.1.1.51
python3 health_check.py 10.1.1.52
python3 health_check.py 10.1.1.53

# All should show ✓ PASS for network and slurmd
```

---

## FAQ

**Q: Can I mix MPI and non-MPI partitions?**

A: Yes! MPI support is per-partition. Non-MPI partitions continue to use asynchronous launch.

**Q: What happens if one node fails health checks?**

A: The entire launch fails, all instances are terminated, and the job is requeued. This prevents partial allocations.

**Q: Can I use spot instances for MPI?**

A: Technically yes, but highly discouraged. Spot interruption kills your entire MPI job with no checkpoint.

**Q: Do I need to modify my MPI application?**

A: No. Your MPI application runs unchanged. The plugin only affects how nodes are launched.

**Q: What's the maximum number of MPI nodes?**

A: Depends on placement group capacity (typically 20-60 for most instance types). Configured via `MaxNodes`.

**Q: Can I use multiple placement groups?**

A: Not currently supported. Would require code changes. Single placement group per partition.

**Q: Does this work with EFA (Elastic Fabric Adapter)?**

A: Yes, if EFA is configured in your launch template. The plugin doesn't auto-configure EFA (yet).

**Q: What if my AMI is slow to boot?**

A: Increase `MPIOptions.TimeoutSeconds` to 600 (10 minutes) and optimize your AMI size.

---

## See Also

- [IMPLEMENTATION_PLAN_MPI.md](IMPLEMENTATION_PLAN_MPI.md) - Technical implementation details
- [Configuration Reference](configuration.md) - Full partitions.json schema
- [Troubleshooting Guide](troubleshooting.md) - General plugin troubleshooting
- [Advanced Usage](advanced-usage.md) - EFA, FSx for Lustre, and more
- [Performance Tuning](performance-tuning.md) - Optimization tips

## Support

For issues with MPI support:

1. Check `/var/log/slurm/aws_plugin.log` for errors
2. Run `python3 health_check.py <node-ip>` to verify node health
3. Review [Troubleshooting](#troubleshooting) section above
4. Open GitHub issue with:
   - Plugin version
   - Slurm version
   - MPI configuration (partitions.json excerpt)
   - Error logs
   - Output of `sinfo -Nel`
