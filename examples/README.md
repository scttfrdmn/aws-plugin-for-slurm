# Example Configurations

This directory contains example `partitions.json` configurations for common use cases.

## Example 1: On-Demand Priority with Spot Overflow

**File**: [example-1-ondemand-and-spot.json](example-1-ondemand-and-spot.json)

Single `aws` partition with 2 node groups:

- **ondemand** node group: Up to 10 nodes used in priority (Slurm `Weight=1`)
- **spot** node group: Up to 100 nodes with lower priority (Slurm `Weight=2`)

The scheduler automatically launches and allocates jobs to Spot instances when all on-demand nodes are running and busy.

**Use case**: Cost optimization while ensuring critical jobs can access on-demand capacity.

## Example 2: Multi-AZ with Availability Zone Selection

**File**: [example-2-multi-az.json](example-2-multi-az.json)

Single `aws` partition with 3 node groups:

- **spot4vCPU**: Default node group (lowest weight) that launches Spot instances with c5.xlarge or c4.xlarge across two availability zones using lowest price strategy
- **spot4vCPUa**: AZ-specific node group for `us-east-1a` (specify with feature flag)
- **spot4vCPUb**: AZ-specific node group for `us-east-1b` (specify with feature flag)

**Use case**: Allow users to run jobs with all nodes in the same availability zone for low-latency inter-node communication, while defaulting to multi-AZ for best spot pricing.

**Example job submission**:
```bash
# Default: spread across AZs
srun -p aws hostname

# Force specific AZ
srun -p aws -C us-east-1a hostname
```

## Example 3: Account-Based Access Control

**File**: [example-3-account-permissions.json](example-3-account-permissions.json)

Two partitions with different access permissions:

- **aws** partition: On-demand instances
  - Accessible by "standard" and "VIP" accounts
  - Higher billing weight (cpu=30) for accounting
- **awsspot** partition: Spot instances
  - Accessible by "standard" account only
  - Lower billing weight (cpu=10) for accounting

**Use case**: Implement tiered access where standard users are restricted to spot instances, while VIP users can access both spot and on-demand instances. The higher billing weight for on-demand instances encourages cost-conscious behavior.

**Example job submission**:
```bash
# Standard users - must use spot
srun -p awsspot hostname

# VIP users - can use spot or on-demand
srun -p awsspot hostname  # Cheaper option
srun -p aws hostname      # More expensive, guaranteed capacity
```

## Example 4: GPU Workloads

**File**: [example-4-gpu-nodes.json](example-4-gpu-nodes.json)

Two partitions with GPU and CPU node groups:

- **gpu** partition with three GPU node groups:
  - **a100**: Up to 10 nodes with p4d.24xlarge (8x A100 GPUs each) - highest priority
  - **v100**: Up to 20 nodes with p3.8xlarge (4x V100 GPUs each) - medium priority
  - **t4**: Up to 50 nodes with g4dn.4xlarge (1x T4 GPU each) - lowest priority, cost-effective
- **cpu** partition: Up to 100 CPU-only nodes for non-GPU workloads (default partition)

The GPU partition uses TRES billing weights to charge more for GPU usage. Weight priorities ensure expensive A100 nodes are only used when cheaper options are exhausted.

**Use case**: ML/AI workloads with multiple GPU tiers for different job requirements. Training jobs can request specific GPU types, while inference workloads use cost-effective T4 instances.

**Example job submission**:
```bash
# Request specific GPU type
srun -p gpu --gres=gpu:a100:2 ./training_job
srun -p gpu --gres=gpu:t4:1 ./inference_job

# Let Slurm choose based on availability
srun -p gpu --gres=gpu:4 ./job

# CPU-only jobs
srun -p cpu ./preprocessing
```

**Requirements**:
- Launch templates must include GPU-enabled AMIs with NVIDIA drivers
- CUDA toolkit pre-installed or loaded via modules
- `GresTypes=gpu` in config.json

## Example 5: MPI / Tightly-Coupled Workloads

**File**: [example-5-mpi-workloads.json](example-5-mpi-workloads.json)

Two partitions demonstrating the v3.1 launch settings:

- **mpi** partition: Up to 64 network-optimized nodes in a cluster placement group, single
  subnet, on-demand, with all-or-nothing launch enabled
- **cpu** partition: Up to 100 spot nodes across two AZs for general work (default partition)

**Use case**: Parallel jobs where a partial allocation is useless. `EnableMPISupport` makes
`resume.py` wait for the whole allocation and terminate it if any node is missing or
unhealthy, rather than leaving idle nodes to be billed until `ResumeTimeout` expires.

Note that v2 already runs MPI jobs correctly — Slurm holds a job in `CONFIGURING` until every
allocated node registers. These settings reduce the cost and opacity of *failed* launches.
See [MPI and Tightly-Coupled Workloads](../docs/mpi-support.md).

**Example job submission**:
```bash
# 4 nodes, 64 ranks each
srun -p mpi -N 4 -n 256 ./mpi_application

# General work on the cheaper spot partition
srun -p cpu ./preprocessing
```

**Requirements**:
- A cluster placement group must exist:
  `aws ec2 create-placement-group --group-name slurm-mpi-pg --strategy cluster`
- Single subnet in `SubnetIds` — cluster placement groups cannot span AZs
- Matching MPI library and version on the AMI across all nodes
- `ResumeTimeout` in config.json must exceed `MPIOptions.TimeoutSeconds` by at least 120s
  (the default 300 needs `ResumeTimeout` >= 420; the shipped template's 300 is too low)

## Example config.json Files

The examples directory also includes sample `config.json` files:

### config-basic.json

**File**: [config-basic.json](config-basic.json)

Basic configuration for CPU-only clusters:
- Standard logging level (INFO)
- Default system paths
- Conservative resume/suspend rates
- 5-minute resume timeout

### config-gpu.json

**File**: [config-gpu.json](config-gpu.json)

Configuration for GPU-enabled clusters:
- Enables GRES GPU support
- Longer resume timeout (10 minutes) for GPU instance launch
- Appropriate for mixed CPU/GPU workloads

### config-production.json

**File**: [config-production.json](config-production.json)

Production-ready configuration:
- WARNING level logging (less verbose)
- Lower resume/suspend rates to avoid API throttling
- Longer timeouts for stability
- Custom Slurm installation paths

## Customizing Examples

To use these examples:

1. **For partitions.json:**
   - Copy the example file to `partitions.json` in your plugin directory
   - Update the following fields for your environment:
     - `LaunchTemplateName` - Your EC2 launch template name
     - `SubnetIds` - Your VPC subnet IDs
     - `Region` - Your AWS region
     - Instance types (optional) - Adjust based on your workload needs

2. **For config.json:**
   - Copy the appropriate config example to `config.json`
   - Update paths to match your Slurm installation
   - Adjust timeouts based on your instance launch times

3. **Generate Slurm configuration:**
   ```bash
   cd /path/to/plugin
   ./generate_conf.py
   ```

4. **Apply configuration:**
   ```bash
   cat slurm.conf.aws >> /etc/slurm/slurm.conf
   scontrol reconfigure
   ```

See the [Configuration Reference](../docs/configuration.md) for detailed parameter descriptions.
