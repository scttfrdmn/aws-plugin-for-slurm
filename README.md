# AWS Plugin for Slurm - Version 3

A Slurm plugin that enables dynamic cloud bursting from on-premises HPC clusters to AWS.

## Overview

[Slurm](https://slurm.schedmd.com/) is a popular HPC cluster management system. This plugin enables your **on-premises Slurm cluster** to dynamically burst into AWS when local compute capacity is exhausted, giving you access to virtually unlimited resources on demand.

**Primary Use Case: On-Premises → AWS Cloud Bursting**

```
┌─────────────────────────────┐         ┌──────────────────────────┐
│  Your Data Center           │         │  AWS Cloud               │
│                             │         │                          │
│  ┌────────────────────┐     │         │  ┌────────────────────┐ │
│  │  Slurm Headnode    │◄────┼─────────┼─►│  Burst Compute     │ │
│  │  (stays on-prem)   │     │  VPN/DX │  │  Nodes (dynamic)   │ │
│  └────────────────────┘     │         │  └────────────────────┘ │
│                             │         │                          │
│  ┌────────────────────┐     │         │  - Launch on demand     │
│  │  Static Compute    │     │         │  - Terminate when idle  │
│  │  (your hardware)   │     │         │  - Pay only for usage   │
│  └────────────────────┘     │         │                          │
└─────────────────────────────┘         └──────────────────────────┘
```

**Benefits:**
- **Keep your existing cluster** - Headnode and static nodes stay on-premises
- **Overflow capacity** - Burst to AWS only when you need it
- **Cost efficient** - Pay for cloud compute only when actually used
- **Access to specialized hardware** - GPU instances, high-memory nodes, etc.
- **No cluster migration** - Works alongside your current infrastructure

## What's New in Version 3

Version 3 focuses on the **real-world use case**: bursting from on-premises to AWS.

**New in v3.1 (tightly-coupled workloads):**
- **All-or-nothing launch** - Terminate short allocations immediately instead of paying for idle nodes until `ResumeTimeout`
- **Placement groups in `partitions.json`** - Configure per node group rather than per launch template
- **Readiness checks** - Name the node and failed check in the log instead of an opaque `DOWN`

Note: v2 already runs MPI jobs correctly — Slurm holds a job in `CONFIGURING` until every
node registers. These additions reduce the cost and opacity of *failed* launches. See
[MPI and Tightly-Coupled Workloads](docs/mpi-support.md).

**New in v3.0:**
- **Comprehensive on-prem bursting guide** - Step-by-step VPN setup, AMI building, troubleshooting
- **Automated AMI builder** - Packer template ensures exact Slurm version match
- **Connectivity validator** - Pre-flight checks for network, NFS, Munge
- **AWS CLI-first documentation** - Infrastructure as code, no clickops
- **Enhanced security** - IMDSv2, Munge via Secrets Manager, least privilege IAM
- **GPU/GRES support** - Complete documentation and examples

**From v2:**
- EC2 Fleet integration for Spot instances and instance type flexibility
- Decoupled node identity from EC2 instance attributes
- Improved error handling and node state management

## Getting Started

### For On-Premises Clusters (Primary Use Case)

**You have**: Existing Slurm cluster on-premises
**You want**: Burst to AWS for overflow capacity

**Start here**: **[On-Premises to AWS Cloud Bursting Guide](docs/onprem-to-aws-bursting.md)** ⭐

This comprehensive guide covers:
1. Establishing network connectivity (VPN/Direct Connect)
2. Building an AMI that matches your Slurm version
3. Configuring NFS access from AWS to on-prem
4. Setting up Munge authentication
5. Testing and validation

**Quick validation** - Before you start, test your connectivity:
```bash
./scripts/validate-onprem-connectivity.sh \
  --headnode 10.0.1.100 \
  --region us-east-1 \
  --vpc vpc-xxxxx \
  --subnet subnet-xxxxx
```

**Automated AMI building** - Use our Packer template:
```bash
cd examples/packer
cp variables.pkrvars.hcl.example variables.pkrvars.hcl
# Edit variables.pkrvars.hcl with your settings
packer build -var-file=variables.pkrvars.hcl slurm-compute-node.pkr.hcl
```

### For All-AWS Deployments (Alternative Use Case)

**You have**: Nothing yet, want to deploy entirely in AWS
**You want**: Self-managed Slurm cluster in AWS

**Consider first**: [AWS ParallelCluster](https://aws.amazon.com/hpc/parallelcluster/) - AWS's managed HPC solution with better AWS integration, auto-scaling, and support.

**If you specifically need this plugin approach**, see:
- **[CloudFormation Quick Start](docs/cloudformation.md)** - Deploy test cluster in ~15 minutes
- **[Manual Installation](docs/manual-installation.md)** - For production all-AWS deployments

**Note**: The CloudFormation template is useful for:
- Testing the plugin before on-prem deployment
- Quick proof-of-concept clusters
- Specific use cases where you inherited this plugin and need to maintain compatibility

## How It Works

The plugin integrates with Slurm's [power save mode](https://slurm.schedmd.com/power_save.html):

1. **Pre-declaration** - Cloud nodes are declared in `slurm.conf` in `CLOUD` state (powered down)
2. **Job submission** - When work is assigned to cloud nodes, Slurm calls `ResumeProgram`
3. **Instance launch** - Plugin creates EC2 Fleet, instances boot and mount NFS from headnode
4. **IP injection** - Plugin uses `scontrol update NodeName=X NodeAddr=IP` to register nodes
5. **Job execution** - Nodes join cluster and process workloads
6. **Auto-termination** - After `SuspendTime` seconds idle, Slurm calls `SuspendProgram` and instances terminate

**Key insight**: No DNS required! The plugin injects private IPs directly into Slurm.

```
┌──────────────┐    Resume     ┌─────────────┐
│ Slurm        │──────────────>│ EC2 Fleet   │
│ Scheduler    │               │ Launch      │
└──────────────┘               └─────────────┘
       │                              │
       │ scontrol update              │ Instances
       │ NodeAddr=10.1.1.50           │ Running
       v                              v
┌──────────────┐    Suspend    ┌─────────────┐
│ Slurm        │──────────────>│ Terminate   │
│ (idle nodes) │               │ Instances   │
└──────────────┘               └─────────────┘
```

## Architecture

### Plugin Components

- **Python Scripts** (run on headnode)
  - `resume.py` - Launch EC2 instances when Slurm resumes nodes
  - `suspend.py` - Terminate instances when Slurm suspends nodes
  - `change_state.py` - Periodic cleanup of stuck nodes (cron)
  - `generate_conf.py` - Generate Slurm configuration from JSON
  - `common.py` - Shared utilities

- **Configuration Files**
  - `config.json` - Plugin and Slurm parameters
  - `partitions.json` - Partition and node group specifications

### Tools Provided

- **`examples/packer/`** - Automated AMI builder (solves the "matching Slurm version" problem)
- **`scripts/validate-onprem-connectivity.sh`** - Pre-deployment connectivity testing
- **`examples/`** - Ready-to-use configuration examples

## Documentation

### Primary Documentation (On-Prem Bursting)
- **[On-Premises to AWS Bursting Guide](docs/onprem-to-aws-bursting.md)** ⭐ - Complete setup guide
- [MPI and Tightly-Coupled Workloads](docs/mpi-support.md) - Placement groups, all-or-nothing launch (v3.1)
- [Configuration Reference](docs/configuration.md) - config.json and partitions.json parameters
- [Troubleshooting Guide](docs/troubleshooting.md) - Common issues and solutions
- [Security Best Practices](docs/security.md) - IAM, network security, Munge key management

### Additional Documentation
- [CloudFormation Deployment](docs/cloudformation.md) - All-AWS cluster deployment
- [Manual Installation](docs/manual-installation.md) - Step-by-step for any deployment
- [Networking Guide](docs/networking.md) - VPC architecture, VPN, Direct Connect
- [Performance Tuning](docs/performance-tuning.md) - Optimization recommendations
- [Monitoring](docs/monitoring.md) - CloudWatch integration
- [Advanced Usage](docs/advanced-usage.md) - Multi-region, GPU, EFA, FSx
- [Testing](docs/testing.md) - Validation procedures
- [Upgrade Guide](docs/upgrade-guide.md) - Migrating from v2

### Examples
- [On-Demand + Spot overflow](examples/example-1-ondemand-and-spot.json)
- [Multi-AZ deployment](examples/example-2-multi-az.json)
- [Account-based permissions](examples/example-3-account-permissions.json)
- [GPU workloads](examples/example-4-gpu-nodes.json)
- [MPI workloads](examples/example-5-mpi-workloads.json) - Placement group + all-or-nothing launch
- [Basic config](examples/config-basic.json)
- [Production config](examples/config-production.json)

## Requirements

### On-Premises Requirements
- **Slurm cluster** with power save mode (tested with 20.02.3+)
- **Network connectivity** to AWS VPC (VPN or Direct Connect)
- **NFS server** accessible from AWS (or use EFS)
- **Munge** authentication configured
- **Python 3.6+** with boto3 and AWS CLI on headnode

### AWS Requirements
- **VPC** with private subnets for compute nodes
- **EC2 launch template(s)** - Use Packer template to build AMI
- **IAM roles** for headnode (or IAM user) and compute nodes
- **VPN or Direct Connect** for connectivity to on-prem

### Critical: AMI Must Match On-Prem Slurm Version

Your AWS AMI **must** have the exact same Slurm version as your on-prem cluster:

```bash
# On your headnode:
slurmctld --version
# slurm 20.02.3

# Your AMI must also be 20.02.3 - not 20.02.4, not 21.08.0, exactly 20.02.3
```

**Solution**: Use our [Packer template](examples/packer/) to automate this.

## Quick Reference

### Test Connectivity
```bash
./scripts/validate-onprem-connectivity.sh \
  --headnode YOUR_HEADNODE_IP \
  --region us-east-1 \
  --vpc YOUR_VPC_ID \
  --subnet YOUR_SUBNET_ID
```

### Build Matching AMI
```bash
cd examples/packer
packer build -var-file=variables.pkrvars.hcl slurm-compute-node.pkr.hcl
```

### Submit Cloud Burst Job
```bash
# After setup is complete:
srun -p cloud hostname
```

### Monitor Cloud Nodes
```bash
# Watch Slurm state
watch sinfo -p cloud

# Watch AWS instances
aws ec2 describe-instances \
  --filters "Name=tag:ManagedBy,Values=Slurm" \
  --query "Reservations[].Instances[].[InstanceId,State.Name,PrivateIpAddress]"
```

## IAM Permissions

### Headnode Role (or IAM User)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:CreateFleet",
        "ec2:RunInstances",
        "ec2:TerminateInstances",
        "ec2:CreateTags",
        "ec2:DescribeInstances"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": "iam:CreateServiceLinkedRole",
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "iam:AWSServiceName": "ec2fleet.amazonaws.com"
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": "arn:aws:iam::ACCOUNT_ID:role/ComputeNodeRole"
    }
  ]
}
```

### Compute Node Role

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "ec2:DescribeTags",
      "Resource": "*"
    }
  ]
}
```

## Common Issues

### "Nodes stuck in alloc# state"
**Cause**: Network connectivity issues or AMI problems
**Solution**: Run connectivity validator, check VPN status, verify AMI Slurm version matches

### "NFS mount fails from AWS"
**Cause**: Firewall blocking port 2049 or NFS not exported to AWS subnet
**Solution**: Check firewall rules, add AWS CIDR to `/etc/exports`

### "Munge authentication fails"
**Cause**: Key mismatch or time skew
**Solution**: Verify md5sum of Munge key matches on-prem, enable chrony/NTP

See [Troubleshooting Guide](docs/troubleshooting.md) for more.

## Support

This is a community-maintained project. For help:

1. Check the [On-Prem Bursting Guide](docs/onprem-to-aws-bursting.md)
2. Review [Troubleshooting](docs/troubleshooting.md)
3. Search [GitHub Issues](https://github.com/scttfrdmn/aws-plugin-for-slurm/issues)
4. Open a new issue with:
   - Plugin version
   - Slurm version (on-prem and AMI)
   - Network setup (VPN/Direct Connect)
   - Error logs from `/var/log/slurm/aws_plugin.log`

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

This project is licensed under MIT-0 (MIT No Attribution), the same license as the
upstream AWS plugin. See [LICENSE](LICENSE) for details.

Original work Copyright 2020 Amazon.com, Inc. or its affiliates
Modified work Copyright 2025 Scott Friedman

## Related Projects

- [AWS ParallelCluster](https://aws.amazon.com/hpc/parallelcluster/) - For all-AWS HPC clusters (recommended over this plugin for AWS-only deployments)
- [Slurm Documentation](https://slurm.schedmd.com/) - Official Slurm documentation
- [EC2 Fleet](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-fleet.html) - AWS EC2 Fleet documentation
