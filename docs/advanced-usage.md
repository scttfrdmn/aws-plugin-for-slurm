# Advanced Usage Guide

This document covers advanced scenarios and configurations for the AWS Plugin for Slurm.

## Multi-Region Deployments

Deploy compute nodes across multiple AWS regions.

### Configuration

**partitions.json:**

```json
{
  "Partitions": [
    {
      "PartitionName": "us-east",
      "NodeGroups": [{
        "NodeGroupName": "compute",
        "MaxNodes": 50,
        "Region": "us-east-1",
        "SubnetIds": ["subnet-east-1", "subnet-east-2"]
      }]
    },
    {
      "PartitionName": "us-west",
      "NodeGroups": [{
        "NodeGroupName": "compute",
        "MaxNodes": 50,
        "Region": "us-west-2",
        "SubnetIds": ["subnet-west-1", "subnet-west-2"]
      }]
    }
  ]
}
```

### Requirements

1. **VPN or Transit Gateway** between regions
2. **Low latency** (< 100ms recommended)
3. **Consistent Munge key** across all nodes
4. **Synchronized clocks** (critical for Munge)

### Use Cases

- **Data locality** - Process data in region where it's stored
- **Capacity** - Access capacity in multiple regions
- **Compliance** - Keep data in specific geographic regions

## Multiple AWS Accounts

Launch compute nodes in different AWS accounts.

### Setup

**IAM Cross-Account Role:**

In target account, create role with trust policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "AWS": "arn:aws:iam::SOURCE_ACCOUNT:root"
    },
    "Action": "sts:AssumeRole"
  }]
}
```

**Configure AWS Profile:**

On headnode:

```bash
# ~/.aws/config
[profile account-a]
role_arn = arn:aws:iam::ACCOUNT_A:role/SlurmLauncher
source_profile = default

[profile account-b]
role_arn = arn:aws:iam::ACCOUNT_B:role/SlurmLauncher
source_profile = default
```

**partitions.json:**

```json
{
  "Partitions": [
    {
      "PartitionName": "account-a",
      "NodeGroups": [{
        "NodeGroupName": "compute",
        "MaxNodes": 50,
        "Region": "us-east-1",
        "ProfileName": "account-a",
        "SubnetIds": ["subnet-xxxxx"]
      }]
    },
    {
      "PartitionName": "account-b",
      "NodeGroups": [{
        "NodeGroupName": "compute",
        "MaxNodes": 50,
        "Region": "us-east-1",
        "ProfileName": "account-b",
        "SubnetIds": ["subnet-yyyyy"]
      }]
    }
  ]
}
```

### Use Cases

- **Cost allocation** by department/project
- **Security isolation**
- **Billing separation**

## Custom Bootstrap Scripts

Advanced userdata configurations for specialized setups.

### Mounting S3 as Filesystem

```bash
#!/bin/bash
# Install s3fs
yum install -y s3fs-fuse

# Mount S3 bucket
echo ACCESS_KEY:SECRET_KEY > /root/.passwd-s3fs
chmod 600 /root/.passwd-s3fs
s3fs my-bucket /mnt/s3 -o passwd_file=/root/.passwd-s3fs,use_cache=/tmp/s3cache

# Auto-mount on reboot
echo "s3fs#my-bucket /mnt/s3 fuse _netdev,allow_other,use_cache=/tmp/s3cache 0 0" >> /etc/fstab
```

### Installing Commercial Software

```bash
#!/bin/bash
# Download from S3 (pre-signed URL or IAM role)
aws s3 cp s3://my-bucket/software.tar.gz /tmp/

# Install
tar -xzf /tmp/software.tar.gz -C /opt/
/opt/software/install.sh --silent

# Configure license
aws secretsmanager get-secret-value \
  --secret-id software/license \
  --query SecretString \
  --output text > /opt/software/license.dat
```

### Container Runtime Setup

```bash
#!/bin/bash
# Install Docker
yum install -y docker
systemctl enable --now docker

# Or Singularity
yum install -y singularity

# Pull common containers
docker pull tensorflow/tensorflow:latest-gpu
singularity pull docker://pytorch/pytorch:latest
```

## Hybrid Cloud Bursting

Burst from on-premises cluster to AWS.

### Architecture

```
[On-Premises Data Center]
     │
     │ Site-to-Site VPN / Direct Connect
     │
     ▼
[AWS VPC - Private Subnets]
[Compute Nodes]
```

### Configuration Steps

1. **Establish connectivity** (VPN/Direct Connect)

2. **Configure on-premises headnode:**

```bash
# Add AWS partition to slurm.conf
cat >> /etc/slurm/slurm.conf <<EOF

# AWS Cloud Burst Partition
PrivateData=CLOUD
ResumeProgram=/opt/slurm/etc/aws/resume.py
SuspendProgram=/opt/slurm/etc/aws/suspend.py
ResumeRate=50
SuspendRate=50
ResumeTimeout=600
SuspendTime=900
TreeWidth=60000

NodeName=aws-burst-[0-99] State=CLOUD CPUs=8
Partition=aws Nodes=aws-burst-[0-99] Default=No MaxTime=INFINITE State=UP
EOF

scontrol reconfigure
```

3. **Configure plugin on headnode:**

Install plugin scripts and configure `partitions.json` to point to AWS subnets.

4. **Configure AWS compute nodes:**

Ensure they can reach on-premises headnode and mount shared filesystem (NFS/EFS).

### Considerations

- **Network latency** - Should be < 50ms
- **Bandwidth** - Adequate for job data transfer
- **Shared filesystem** - Consider EFS or FSx instead of on-prem NFS
- **Cost** - Data transfer costs (in/out of AWS)

## Dedicated Hosts

Use Dedicated Hosts for licensing requirements.

### Allocate Dedicated Host

```bash
aws ec2 allocate-hosts \
  --instance-type c5.2xlarge \
  --availability-zone us-east-1a \
  --quantity 1
```

### Configure Launch Template

```json
{
  "Placement": {
    "Tenancy": "host",
    "HostId": "h-0123456789abcdef0"
  }
}
```

### Use Cases

- Software with per-socket licensing
- Compliance requiring dedicated hardware
- Consistent physical placement

## Capacity Reservations

Reserve capacity for critical workloads.

### Create Capacity Reservation

```bash
aws ec2 create-capacity-reservation \
  --instance-type c5.4xlarge \
  --instance-platform Linux/UNIX \
  --availability-zone us-east-1a \
  --instance-count 10 \
  --instance-match-criteria targeted
```

### Use in Launch Template

```json
{
  "CapacityReservationSpecification": {
    "CapacityReservationTarget": {
      "CapacityReservationId": "cr-0123456789abcdef0"
    }
  }
}
```

### Use Cases

- Guaranteed capacity for time-critical jobs
- Avoid InsufficientCapacity errors
- Compliance SLAs

## Placement Groups

Optimize for low-latency MPI workloads.

### Create Placement Group

```bash
aws ec2 create-placement-group \
  --group-name slurm-mpi-cluster \
  --strategy cluster
```

### Configure in Launch Template

```json
{
  "Placement": {
    "GroupName": "slurm-mpi-cluster"
  }
}
```

### Partition for MPI Jobs

**slurm.conf:**

```bash
NodeName=mpi-[0-99] State=CLOUD CPUs=72 Feature=lowlatency
Partition=mpi Nodes=mpi-[0-99] Default=No MaxTime=INFINITE State=UP
```

### Job Submission

```bash
# Request low-latency nodes
srun -p mpi -C lowlatency -N 10 mpi_application
```

## EFA (Elastic Fabric Adapter)

High-performance networking for HPC.

### Requirements

- EFA-supported instance types (c5n, p4d, etc.)
- EFA-enabled AMI
- EFA drivers installed

### Launch Template

```json
{
  "NetworkInterfaces": [{
    "DeviceIndex": 0,
    "InterfaceType": "efa",
    "Groups": ["sg-xxxxx"],
    "SubnetId": "subnet-xxxxx"
  }]
}
```

### Install EFA Drivers

```bash
#!/bin/bash
# In AMI or user data
curl -O https://efa-installer.amazonaws.com/aws-efa-installer-latest.tar.gz
tar -xf aws-efa-installer-latest.tar.gz
cd aws-efa-installer
./efa_installer.sh -y

# Verify
fi_info -p efa
```

### MPI with EFA

```bash
# Using Intel MPI
mpirun -np 128 -ppn 16 -hosts node[1-8] \
  -genv FI_PROVIDER=efa \
  -genv FI_EFA_USE_DEVICE_RDMA=1 \
  ./application

# Using OpenMPI
mpirun -np 128 --hostfile hosts \
  --mca btl ^openib \
  --mca mtl ofi \
  --mca pml cm \
  ./application
```

## FSx for Lustre Integration

High-performance parallel filesystem.

### Create FSx Filesystem

```bash
aws fsx create-file-system \
  --file-system-type LUSTRE \
  --storage-capacity 1200 \
  --subnet-ids subnet-xxxxx \
  --lustre-configuration \
    DeploymentType=SCRATCH_2,ImportPath=s3://my-bucket,ExportPath=s3://my-bucket/exports
```

### Mount on Compute Nodes

```bash
#!/bin/bash
# Install Lustre client
amazon-linux-extras install -y lustre2.10

# Mount FSx
FSX_DNS=$(aws fsx describe-file-systems \
  --file-system-ids fs-xxxxx \
  --query 'FileSystems[0].DNSName' \
  --output text)

mkdir /fsx
mount -t lustre $FSX_DNS@tcp:/fsx /fsx

# Add to fstab
echo "$FSX_DNS@tcp:/fsx /fsx lustre defaults,_netdev 0 0" >> /etc/fstab
```

### Benefits

- 100s of GB/s throughput
- Sub-millisecond latencies
- S3 integration
- Scales to PBs

## Custom Scheduling Policies

Advanced Slurm scheduling configurations.

### Priority-Based Scheduling

**slurm.conf:**

```bash
# Enable multifactor priority
PriorityType=priority/multifactor

# Weights
PriorityWeightAge=1000
PriorityWeightFairshare=10000
PriorityWeightJobSize=500
PriorityWeightQOS=5000

# Favor small jobs
PriorityFavorSmall=YES
PriorityMaxAge=7-0
```

### QOS (Quality of Service)

```bash
# Create QOS levels
sacctmgr add qos normal priority=100 maxwall=24:00:00 maxsubmitpu=1000
sacctmgr add qos high priority=1000 maxwall=48:00:00 maxsubmitpu=100
sacctmgr add qos low priority=10 maxwall=12:00:00

# Assign to users
sacctmgr add user alice qos=high
sacctmgr add user bob qos=normal
```

### Job Preemption

```bash
# slurm.conf
PreemptMode=REQUEUE
PreemptType=preempt/qos

# Create preemptable QOS
sacctmgr add qos preempt priority=50 Preempt=normal,low
```

### Resource Limits

```bash
# Per-user limits
sacctmgr add user alice maxjobs=100 maxnodes=50

# Per-account limits
sacctmgr add account research-dept maxjobs=500 maxnodes=200
```

## Job Arrays for Parameter Sweeps

Efficiently run many similar jobs.

### Submit Job Array

```bash
#!/bin/bash
#SBATCH --job-name=parameter_sweep
#SBATCH --array=1-1000
#SBATCH --output=output_%A_%a.txt
#SBATCH --partition=aws
#SBATCH --nodes=1

# Each task gets unique SLURM_ARRAY_TASK_ID
PARAM=$(sed -n "${SLURM_ARRAY_TASK_ID}p" parameters.txt)

./simulation --param $PARAM
```

**parameters.txt:**

```
param1
param2
...
param1000
```

### Array Job Control

```bash
# Submit
sbatch job_array.sh

# Limit concurrent running tasks
#SBATCH --array=1-1000%50   # Max 50 running at once

# Cancel specific tasks
scancel JOBID_[10-20]
```

## Checkpointing for Spot Instances

Handle Spot interruptions gracefully.

### DMTCP (Distributed MultiThreaded CheckPointing)

```bash
# Install DMTCP
yum install -y dmtcp

# Run application with checkpointing
dmtcp_launch --interval 600 ./application

# Application will checkpoint every 10 minutes
# Can resume from checkpoint if interrupted
```

### Application-Level Checkpointing

```python
# In your application
import os
import pickle
import signal

def save_checkpoint(state, filename):
    with open(filename, 'wb') as f:
        pickle.dump(state, f)

def load_checkpoint(filename):
    if os.path.exists(filename):
        with open(filename, 'rb') as f:
            return pickle.load(f)
    return None

# Handle SIGTERM (Spot termination warning)
def handle_termination(signum, frame):
    print("Received termination signal, saving checkpoint...")
    save_checkpoint(current_state, 'checkpoint.pkl')
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_termination)

# Resume from checkpoint if exists
state = load_checkpoint('checkpoint.pkl')
if state:
    print("Resuming from checkpoint")
else:
    print("Starting from beginning")
    state = initial_state()
```

### Slurm Checkpointing Configuration

```bash
# slurm.conf
CheckpointType=checkpoint/blcr  # or checkpoint/none for app-level

# Job script
#SBATCH --requeue
#SBATCH --signal=B:USR1@120  # Send USR1 120 seconds before time limit
```

## Next Steps

- Review [Performance Tuning](performance-tuning.md) for optimization
- Check [Security](security.md) for advanced security configurations
- See [Troubleshooting](troubleshooting.md) for common issues
