# Performance Tuning Guide

This document provides guidance on optimizing the AWS Plugin for Slurm for performance, scalability, and cost-efficiency.

## Overview

Performance tuning involves optimizing:
- Instance launch speed
- Scheduler responsiveness
- API rate limits
- Network throughput
- Cost efficiency

## Plugin Configuration Tuning

### ResumeRate and SuspendRate

These parameters control how many instances can be launched/terminated per minute.

**config.json:**

```json
{
  "SlurmConf": {
    "ResumeRate": 100,
    "SuspendRate": 100
  }
}
```

**Recommendations by cluster size:**

| Cluster Size | ResumeRate | SuspendRate | Rationale |
|--------------|------------|-------------|-----------|
| Small (< 50 nodes) | 100 | 100 | Default, unlikely to hit limits |
| Medium (50-500) | 50-100 | 100 | Balance speed vs rate limits |
| Large (500-2000) | 30-50 | 50-100 | Avoid throttling |
| Very Large (2000+) | 20-30 | 50 | Prevent sustained throttling |

**Too high:**
- Risk hitting EC2 API rate limits
- May cause `RequestLimitExceeded` errors
- Plugin retries, but causes delays

**Too low:**
- Slow cluster scale-up
- Jobs wait longer for resources

**Monitor and adjust:**

```bash
# Check for throttling in plugin logs
grep "RequestLimitExceeded" /var/log/slurm/aws_plugin.log

# If throttled, reduce ResumeRate by 20-30%
```

### ResumeTimeout

Time (in seconds) before Slurm marks unresponsive nodes as DOWN.

**Factors affecting timeout:**

1. **Instance launch time** (60-120 seconds)
2. **User data script execution** (varies)
3. **Slurm daemon startup** (10-30 seconds)
4. **Network connectivity** (varies)

**Recommendations:**

| Scenario | ResumeTimeout | Notes |
|----------|---------------|-------|
| CPU instances, pre-baked AMI | 300 (5 min) | Fast launch |
| CPU instances, bootstrap scripts | 600 (10 min) | Software installation |
| GPU instances, pre-baked AMI | 600 (10 min) | Slower launch |
| GPU instances, bootstrap | 900 (15 min) | Driver installation |
| Hybrid (on-prem headnode) | 600-900 | Account for network latency |

**Too low:**
- Nodes marked DOWN before ready
- Wastes instances and time

**Too high:**
- Failed nodes stay in queue longer
- Jobs delayed waiting for failed nodes

**Optimal value:**
```
ResumeTimeout = (P95 launch time) + (P95 bootstrap time) + 60 seconds buffer
```

Measure your actual launch times:

```bash
# Time from CreateFleet to slurmd registration
grep "Launched node" /var/log/slurm/aws_plugin.log | \
  awk '{print $1, $2}' | \
  # Compare with slurmctld logs showing node registration
```

### SuspendTime

Idle time before nodes are terminated.

**Trade-offs:**

**Short (< 300s):**
- ✅ Lower costs (nodes terminated quickly)
- ✅ Faster resource recycling
- ❌ May terminate before next job arrives
- ❌ More instance churn (launch overhead)

**Long (> 900s):**
- ✅ Nodes available for quick job turnaround
- ✅ Less instance churn
- ❌ Higher costs (idle time charges)
- ❌ Slower resource recycling

**Recommendations:**

| Workload Pattern | SuspendTime | Rationale |
|------------------|-------------|-----------|
| Continuous queue | 600-900s | Jobs arriving constantly |
| Bursty workload | 300-600s | Balance cost vs availability |
| Batch overnight | 180-300s | Minimize idle costs |
| Interactive | 900-1800s | Keep resources warm |

**Formula:**
```
SuspendTime = (Average job interval) + (Job submission variance)
```

If jobs arrive every 5 minutes on average:
- `SuspendTime = 300s` (5 min) = nodes always available
- `SuspendTime = 180s` (3 min) = some relaunches, cost savings

**Important:**
```
SuspendTime >= SuspendTimeout (30s default) + ResumeTimeout
```

### TreeWidth

Controls Slurm's communication tree fan-out.

```json
{
  "SlurmConf": {
    "TreeWidth": 60000
  }
}
```

**What it does:**
- Determines how many nodes communicate directly with slurmctld
- Higher = flatter tree = less latency

**Recommendations:**

| Cluster Size | TreeWidth | Reason |
|--------------|-----------|--------|
| < 1000 nodes | 60000 | Flat tree, minimal overhead |
| 1000-10000 | 60000 | Still manageable |
| > 10000 | 10000-50000 | May need tuning based on network |

**For cloud deployments**, 60000 is usually optimal (flat tree).

## EC2 Fleet Optimization

### Allocation Strategies

#### Spot Instances

**lowest-price:**
```json
{
  "SpotOptions": {
    "AllocationStrategy": "lowest-price"
  }
}
```
- Prioritizes lowest-cost pools
- Good for: Cost-sensitive workloads
- Risk: Higher interruption rate

**price-capacity-optimized (Recommended):**
```json
{
  "SpotOptions": {
    "AllocationStrategy": "price-capacity-optimized"
  }
}
```
- Balances price and capacity availability
- Good for: Production workloads
- Best: Fewer interruptions, good pricing

**capacity-optimized:**
```json
{
  "SpotOptions": {
    "AllocationStrategy": "capacity-optimized"
  }
}
```
- Prioritizes deepest capacity pools
- Good for: Interruption-sensitive workloads
- Note: Slightly higher cost

#### On-Demand Instances

**lowest-price:**
```json
{
  "OnDemandOptions": {
    "AllocationStrategy": "lowest-price"
  }
}
```
- Launches cheapest instance type first
- Good for: Cost optimization with diversification

**prioritized:**
```json
{
  "OnDemandOptions": {
    "AllocationStrategy": "prioritized"
  },
  "LaunchTemplateOverrides": [
    {
      "InstanceType": "c5.xlarge",
      "Priority": 1
    },
    {
      "InstanceType": "c6i.xlarge",
      "Priority": 2
    }
  ]
}
```
- Explicit priority order
- Good for: Performance-critical workloads

### Instance Type Diversification

**Benefits:**
- Reduces Insufficient Capacity errors
- Increases Spot availability
- Better pricing opportunities

**Recommendations:**

```json
{
  "LaunchTemplateOverrides": [
    {"InstanceType": "c5.2xlarge"},
    {"InstanceType": "c5a.2xlarge"},
    {"InstanceType": "c5n.2xlarge"},
    {"InstanceType": "c6i.2xlarge"},
    {"InstanceType": "c6a.2xlarge"}
  ]
}
```

**Guidelines:**
- Choose similar-performance instance types
- Mix generations (c5, c6i, c6a)
- Mix variants (c5, c5n, c5a)
- Match vCPU/memory ratios
- Avoid mixing drastically different specs

**For GPU workloads:**

```json
{
  "LaunchTemplateOverrides": [
    {"InstanceType": "p3.2xlarge"},   // V100, 1 GPU
    {"InstanceType": "p3.8xlarge"},   // V100, 4 GPUs
    {"InstanceType": "g4dn.xlarge"}   // T4, 1 GPU (fallback)
  ]
}
```

**Note:** Ensure Slurm specifications match all instance types.

### Spot Instance Best Practices

1. **Use multiple AZs** (diversification)
2. **Use price-capacity-optimized** strategy
3. **Diversify instance types** (5-10 types)
4. **Set reasonable max price** (or omit for on-demand price)
5. **Handle interruptions** gracefully (Slurm does this automatically)

**Interruption handling:**

Spot interruptions are handled automatically:
- Slurm receives 2-minute warning via instance metadata
- Jobs are requeued
- Node is drained and terminated

**No special configuration needed** - the plugin handles this.

## AMI Optimization

### Pre-Baking Software

**Benefits:**
- Faster instance launch (30-120s improvement)
- Reduced ResumeTimeout
- More consistent launch times
- Lower network costs (no repeated downloads)

**What to pre-install:**

```bash
# Essential
- Slurm (slurmd binary)
- Munge
- NFS client
- Python 3 + boto3

# Workload-specific
- Compilers (gcc, gfortran)
- MPI libraries (OpenMPI, MPICH)
- Scientific libraries (BLAS, LAPACK)
- Application software

# GPU nodes
- NVIDIA drivers
- CUDA toolkit
- cuDNN
```

**Example Packer template for AMI:**

```json
{
  "builders": [{
    "type": "amazon-ebs",
    "ami_name": "slurm-compute-{{timestamp}}",
    "instance_type": "c5.xlarge",
    "region": "us-east-1",
    "source_ami_filter": {
      "filters": {
        "name": "amzn2-ami-hvm-*-x86_64-gp2"
      },
      "most_recent": true
    },
    "ssh_username": "ec2-user"
  }],
  "provisioners": [{
    "type": "shell",
    "inline": [
      "sudo yum update -y",
      "sudo yum install -y munge nfs-utils python3",
      "sudo pip3 install boto3"
    ]
  }]
}
```

**GPU AMI:**

```bash
# Base: AWS Deep Learning AMI (has NVIDIA drivers, CUDA)
# or install manually:
sudo yum install -y gcc kernel-devel-$(uname -r)
wget https://us.download.nvidia.com/XFree86/Linux-x86_64/515.65.01/NVIDIA-Linux-x86_64-515.65.01.run
sudo sh NVIDIA-Linux-x86_64-515.65.01.run --silent
```

### User Data Script Optimization

If not pre-baking everything, optimize user data:

**Parallelize installations:**

```bash
#!/bin/bash
set -e

# Run in parallel
(
  yum install -y package1 package2 &
  yum install -y package3 package4 &
  pip3 install library1 library2 &
  wait
)

# Sequential only when dependencies exist
configure_application
start_slurmd
```

**Use faster mirrors:**

```bash
# Use regional mirrors
sed -i 's|http://.*amazon|http://s3-regional-mirror|' /etc/yum.repos.d/*
```

**Cache installations:**

```bash
# Download once, cache in S3
if ! aws s3 cp s3://my-bucket/software.tar.gz /tmp/; then
  wget https://example.com/software.tar.gz
  aws s3 cp software.tar.gz s3://my-bucket/
fi
tar -xzf /tmp/software.tar.gz
```

## Instance Selection

### Compute-Optimized (General HPC)

| Instance | vCPUs | Memory | Network | Use Case | Cost (On-Demand) |
|----------|-------|--------|---------|----------|------------------|
| c5.large | 2 | 4 GB | Up to 10G | Light compute | $0.085/hr |
| c5.xlarge | 4 | 8 GB | Up to 10G | General compute | $0.170/hr |
| c5.2xlarge | 8 | 16 GB | Up to 10G | Medium compute | $0.340/hr |
| c5.4xlarge | 16 | 32 GB | Up to 10G | Heavy compute | $0.680/hr |
| c5n.18xlarge | 72 | 192 GB | 100 Gbps | HPC, MPI | $3.888/hr |

**Recommendations:**
- **General workloads**: c5.xlarge or c5.2xlarge
- **MPI/HPC**: c5n.18xlarge (100 Gbps network)
- **Cost-optimized**: c5a, c6a (AMD, 10-15% cheaper)

### Memory-Optimized

| Instance | vCPUs | Memory | Use Case | Cost |
|----------|-------|--------|----------|------|
| r5.xlarge | 4 | 32 GB | Memory-intensive | $0.252/hr |
| r5.4xlarge | 16 | 128 GB | Large datasets | $1.008/hr |
| x2idn.32xlarge | 128 | 2048 GB | Massive memory | $26.676/hr |

### GPU Instances

| Instance | GPUs | GPU Type | vCPUs | Memory | Use Case | Cost |
|----------|------|----------|-------|--------|----------|------|
| g4dn.xlarge | 1 | T4 | 4 | 16 GB | Inference, light training | $0.526/hr |
| p3.2xlarge | 1 | V100 | 8 | 61 GB | Deep learning training | $3.06/hr |
| p4d.24xlarge | 8 | A100 | 96 | 1152 GB | Large-scale ML | $32.77/hr |
| g5.xlarge | 1 | A10G | 4 | 16 GB | Graphics, ML inference | $1.006/hr |

**Recommendations:**
- **Training**: p3, p4d (V100, A100)
- **Inference**: g4dn (T4 - cost-effective)
- **Graphics + ML**: g5 (A10G)

### Spot Pricing Guidance

**Spot discounts** (vs on-demand):
- Compute-optimized: 60-70% savings
- Memory-optimized: 70-80% savings
- GPU instances: 50-70% savings

**Check current Spot pricing:**

```bash
aws ec2 describe-spot-price-history \
  --instance-types c5.2xlarge c5a.2xlarge c6i.2xlarge \
  --start-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --product-descriptions "Linux/UNIX" \
  --query 'SpotPriceHistory[*].[AvailabilityZone,InstanceType,SpotPrice]' \
  --output table
```

## Slurm Configuration Tuning

### Scheduler Configuration

**In slurm.conf:**

```bash
# Use backfill for better scheduling
SchedulerType=sched/backfill
SchedulerTimeSlice=30

# Backfill parameters
SchedulerParameters=bf_max_job_test=1000,bf_interval=30

# Faster scheduling decisions
SelectType=select/cons_tres
SelectTypeParameters=CR_CPU_Memory

# Resource allocation
DefMemPerCPU=2048
MaxMemPerCPU=16384
```

**Explanation:**
- `bf_max_job_test` - Check up to 1000 jobs per cycle (increase for large queues)
- `bf_interval` - Backfill runs every 30 seconds (decrease for faster response)
- `cons_tres` - Consumable resources (CPU, memory, GPU)

### Job Prioritization

```bash
# Enable multifactor priority
PriorityType=priority/multifactor
PriorityWeightAge=1000
PriorityWeightFairshare=10000
PriorityWeightJobSize=500
PriorityWeightPartition=1000
PriorityWeightQOS=5000

# Favor smaller jobs (better packing)
PriorityFavorSmall=YES

# Age factor (seconds to double age priority)
PriorityMaxAge=7-0  # 7 days
```

### Accounting Database

Use `slurmdbd` for better scheduling decisions:

```bash
# In slurm.conf
AccountingStorageType=accounting_storage/slurmdbd
AccountingStorageHost=headnode
AccountingStoragePort=6819

# Fair-share scheduling
PriorityType=priority/multifactor
AccountingStorageEnforce=associations,limits,qos
```

**Benefits:**
- Fair-share scheduling
- Historical usage tracking
- Better backfill decisions
- QOS-based priorities

## Network Performance

### Jumbo Frames

Enable MTU 9001 for better throughput:

**On headnode and compute nodes:**

```bash
# Check current MTU
ip link show eth0

# Set MTU 9001 (jumbo frames)
sudo ip link set dev eth0 mtu 9001

# Persist in /etc/sysconfig/network-scripts/ifcfg-eth0
echo "MTU=9001" | sudo tee -a /etc/sysconfig/network-scripts/ifcfg-eth0

# Test
ping -M do -s 8973 headnode  # Should succeed
```

**In launch template user data:**

```bash
#!/bin/bash
ip link set dev eth0 mtu 9001
echo "MTU=9001" >> /etc/sysconfig/network-scripts/ifcfg-eth0
```

### TCP Tuning

Optimize TCP for high-throughput workloads:

```bash
# /etc/sysctl.conf
net.core.rmem_max = 134217728
net.core.wmem_max = 134217728
net.ipv4.tcp_rmem = 4096 87380 67108864
net.ipv4.tcp_wmem = 4096 65536 67108864
net.ipv4.tcp_congestion_control = bbr
net.core.netdev_max_backlog = 5000

# Apply
sysctl -p
```

### Placement Groups

For latency-sensitive workloads (MPI):

```json
{
  "Placement": {
    "GroupName": "slurm-cluster-pg",
    "Strategy": "cluster"
  }
}
```

**Limitations:**
- Single AZ only
- Limited to 500-700 instances (depends on type)
- Not all instance types supported

**When to use:**
- Tightly-coupled MPI applications
- Sub-millisecond latency requirements
- High packet-per-second workloads

## Storage Performance

### NFS Performance Tuning

**Server (headnode) optimizations:**

```bash
# /etc/nfs.conf
[nfsd]
threads=16
vers4=y
vers4.2=y

# /etc/exports
/nfs *(rw,async,no_subtree_check,no_root_squash,fsid=0)

# Restart NFS
systemctl restart nfs-server
```

**Client (compute nodes):**

```bash
# Mount with optimal options
mount -t nfs4 -o \
  rw,relatime,vers=4.2,rsize=1048576,wsize=1048576,namlen=255,\
  hard,proto=tcp,timeo=600,retrans=2 \
  headnode:/nfs /nfs
```

**Performance testing:**

```bash
# Sequential write
dd if=/dev/zero of=/nfs/testfile bs=1M count=1000

# Sequential read
dd if=/nfs/testfile of=/dev/null bs=1M

# Should see 100-300 MB/s on c5.xlarge
```

### EFS Performance

If using Amazon EFS instead of NFS:

**Throughput modes:**
- **Bursting** - Default, scales with size (50 MB/s per TB)
- **Provisioned** - Fixed throughput (up to 1 GB/s)
- **Elastic** - Scales automatically, pay per GB transferred

**For HPC:**

```bash
# Create EFS with provisioned throughput
aws efs create-file-system \
  --performance-mode maxIO \
  --throughput-mode provisioned \
  --provisioned-throughput-in-mibps 1024
```

**Mount options:**

```bash
mount -t efs -o \
  tls,nfsvers=4.1,rsize=1048576,wsize=1048576 \
  fs-xxxxx:/ /nfs
```

## Monitoring and Profiling

### Track Launch Times

Add metrics collection to resume.py:

```python
import time
start_time = time.time()

# ... launch instances ...

launch_time = time.time() - start_time
logger.info(f"Launch completed in {launch_time:.2f} seconds")
```

**Analyze:**

```bash
grep "Launch completed" /var/log/slurm/aws_plugin.log | \
  awk '{print $NF}' | \
  awk '{sum+=$1; count++} END {print "Avg:", sum/count, "Count:", count}'
```

### Monitor API Throttling

```bash
# Count throttling events
grep -c "RequestLimitExceeded" /var/log/slurm/aws_plugin.log

# If > 0, reduce ResumeRate
```

### Scheduler Performance

```bash
# Check scheduler cycle time
grep "sched: SchedulerCycleTime" /var/log/slurm/slurmctld.log

# Should be < 1 second for good performance
# If > 2 seconds, tune SchedulerParameters
```

## Cost Optimization

While not covering full cost management (per your request), here are performance-related cost optimizations:

### Right-Sizing Instances

Use Slurm accounting to identify underutilized nodes:

```bash
# Show average CPU utilization per job
sacct --format=JobID,NodeList,CPUTime,CPUTimeRAW,TotalCPU --state=COMPLETED

# If < 50% CPU utilization, consider smaller instances
```

### Spot vs On-Demand Mix

Balance cost and reliability:

```json
{
  "NodeGroups": [
    {
      "NodeGroupName": "ondemand",
      "MaxNodes": 10,
      "PurchasingOption": "on-demand",
      "SlurmSpecifications": {"Weight": "1"}
    },
    {
      "NodeGroupName": "spot",
      "MaxNodes": 100,
      "PurchasingOption": "spot",
      "SlurmSpecifications": {"Weight": "2"}
    }
  ]
}
```

**Slurm uses on-demand first (Weight=1), then spot (Weight=2).**

## Benchmarking

### Instance Launch Benchmark

```bash
# Measure time to launch 10 nodes
time srun -N10 --exclusive hostname

# Measure time to scale to 100 nodes
time srun -N100 --exclusive hostname &
watch -n1 'squeue | grep -c R'
```

### Application Performance

```bash
# HPL (High Performance Linpack) benchmark
mpirun -np 64 ./xhpl

# IOR (I/O benchmark)
mpirun -np 32 ior -a POSIX -b 1m -t 1m -s 1024 -F

# OSU MPI benchmarks
mpirun -np 2 -hostfile hosts osu_latency
mpirun -np 2 -hostfile hosts osu_bw
```

## Performance Checklist

### Plugin Configuration
- [ ] ResumeRate tuned for cluster size
- [ ] SuspendRate appropriate
- [ ] ResumeTimeout accounts for launch + bootstrap time
- [ ] SuspendTime balances cost vs availability
- [ ] TreeWidth set to 60000

### EC2 Fleet
- [ ] Using price-capacity-optimized for Spot
- [ ] 5-10 instance types in LaunchTemplateOverrides
- [ ] Multiple AZs in SubnetIds
- [ ] Instance types match workload requirements

### AMI
- [ ] Software pre-installed (minimal user data)
- [ ] NVIDIA drivers pre-installed (GPU instances)
- [ ] AMI updated regularly for security patches

### Network
- [ ] Jumbo frames enabled (MTU 9001)
- [ ] Placement groups (if needed for MPI)
- [ ] Enhanced networking verified
- [ ] TCP tuning applied

### Slurm
- [ ] Backfill scheduler enabled
- [ ] Reasonable backfill parameters
- [ ] Accounting database configured
- [ ] Fair-share scheduling enabled

### Storage
- [ ] NFS optimized (threads, vers4.2, async)
- [ ] Or using EFS with appropriate throughput mode
- [ ] Tested I/O performance meets requirements

### Monitoring
- [ ] CloudWatch metrics enabled
- [ ] Plugin logs monitored for throttling
- [ ] Scheduler cycle time monitored
- [ ] Launch times tracked

## Next Steps

- Implement [Monitoring](monitoring.md) for ongoing optimization
- Review [Advanced Usage](advanced-usage.md) for complex scenarios
- Check [Troubleshooting](troubleshooting.md) for common issues
