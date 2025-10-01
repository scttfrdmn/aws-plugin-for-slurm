# AWS Plugin for Slurm - Version 3

A Slurm plugin that enables dynamic cloud bursting and elastic HPC clusters using AWS EC2 Fleet.

## Overview

[Slurm](https://slurm.schedmd.com/) is a popular HPC cluster management system. This plugin enables Slurm to dynamically launch and terminate compute resources in AWS, taking advantage of cloud elasticity and pay-per-use pricing.

**Key Features:**
- Support for EC2 Spot and On-Demand instances
- Instance type diversification via EC2 Fleet
- Decoupled node names from hostnames/IPs
- Robust error handling for failed launches
- Works with headnodes located anywhere (on-premises or cloud)

**Use Cases:**
- **Cloud Bursting** - Dynamically allocate AWS resources alongside on-premises infrastructure
- **Elastic HPC Clusters** - Deploy fully cloud-based HPC environments as an alternative to managed solutions like [AWS ParallelCluster](https://aws.amazon.com/hpc/parallelcluster/)

## What's New in Version 3

Version 3 is a complete rewrite of the [original plugin](https://github.com/aws-samples/aws-plugin-for-slurm) (2018) with major improvements:

- EC2 Fleet integration for Spot instances and instance type flexibility
- Decoupled node identity from EC2 instance attributes
- Improved error handling and node state management
- Better documentation and example configurations

## Quick Start

The fastest way to try the plugin is using the CloudFormation template to deploy a pre-configured headnode.

### Prerequisites

- AWS account with VPC and subnets
- Two subnets in different availability zones
- SSH key pair (optional, for accessing the headnode)

### Deploy with CloudFormation

1. Create a CloudFormation stack using [`template.yaml`](template.yaml)
2. Provide parameters:
   - VPC ID
   - Two subnet IDs (in different availability zones)
   - (Optional) SSH key pair

The stack creates:
- Security group (allows SSH and inter-node traffic)
- IAM roles for headnode and compute nodes
- Launch template for compute nodes
- Headnode instance with pre-configured plugin

### Test the Deployment

1. Connect to the headnode via SSH (instance ID is in CloudFormation outputs)

2. Submit a test job:
   ```bash
   srun -p aws hostname
   ```

3. Monitor instance launch in the EC2 console

4. After job completion, the node will idle for `SuspendTime` seconds before terminating

The sample configuration includes:
- Single partition: `aws`
- Single node group: `node`
- Up to 100 on-demand instances

## How It Works

The plugin integrates with Slurm's [power save mode](https://slurm.schedmd.com/power_save.html):

1. **Node Declaration** - All potential cloud nodes are pre-declared in Slurm configuration in `CLOUD` power save state
2. **Job Submission** - When work is assigned to cloud nodes, Slurm calls `ResumeProgram`
3. **Launch Instances** - `ResumeProgram` creates an EC2 Fleet and updates node IP addresses in Slurm
4. **Run Jobs** - Nodes become available and process jobs
5. **Idle Timeout** - After `SuspendTime` seconds of idle, Slurm calls `SuspendProgram`
6. **Terminate Instances** - `SuspendProgram` terminates EC2 instances and returns nodes to power save state

```
┌──────────────┐    Resume     ┌─────────────┐
│ Slurm        │──────────────>│ EC2 Fleet   │
│ Scheduler    │               │ Launch      │
└──────────────┘               └─────────────┘
       │                              │
       │ Job Complete                 │ Instances
       │ + Idle Time                  │ Running
       v                              v
┌──────────────┐    Suspend    ┌─────────────┐
│ Slurm        │──────────────>│ Terminate   │
│ SuspendTime  │               │ Instances   │
└──────────────┘               └─────────────┘
```

## Architecture

The plugin consists of:

- **Python Scripts**
  - `resume.py` - Launch EC2 instances when Slurm resumes nodes
  - `suspend.py` - Terminate instances when Slurm suspends nodes
  - `change_state.py` - Periodic cleanup of stuck nodes (runs via cron)
  - `generate_conf.py` - Generate Slurm configuration from JSON
  - `common.py` - Shared utilities

- **Configuration Files**
  - `config.json` - Plugin and Slurm parameters
  - `partitions.json` - Partition and node group specifications

All files must be in the same directory on the headnode.

## Installation

Choose your installation method:

- **[Quick Start with CloudFormation](#quick-start)** (recommended for testing)
- **[Manual Installation](docs/manual-installation.md)** (for production deployments)

## Configuration

### Basic Configuration

1. Create `config.json` with plugin settings
2. Create `partitions.json` defining node groups
3. Run `generate_conf.py` to create Slurm config
4. Append generated config to `slurm.conf`
5. Reconfigure Slurm

### Configuration Files

See the [Configuration Reference](docs/configuration.md) for detailed parameter descriptions.

### Example Configurations

The [examples](examples/) directory contains ready-to-use configurations:

- [On-Demand priority with Spot overflow](examples/example-1-ondemand-and-spot.json)
- [Multi-AZ with AZ-specific node groups](examples/example-2-multi-az.json)
- [Account-based access control](examples/example-3-account-permissions.json)

## Plugin Components

### resume.py

The `ResumeProgram` executed by Slurm to launch instances:

1. Retrieves the list of nodes to resume
2. Groups nodes by partition and node group
3. Creates EC2 Fleet for each group
4. Tags instances with node names
5. Updates node IP addresses in Slurm via `scontrol`

**Manual testing:**
```bash
/path/to/resume.py partition-nodegroup-0
```

### suspend.py

The `SuspendProgram` executed by Slurm to terminate instances:

1. Retrieves the list of nodes to suspend
2. Finds EC2 instance ID for each node (via `Name` tag)
3. Terminates the instances

**Manual testing:**
```bash
/path/to/suspend.py partition-nodegroup-0
```

### change_state.py

Periodic maintenance script (run via cron every minute):

- Changes state of nodes stuck in transient states
- Moves `DOWN*` nodes to `POWER_DOWN` state
- Handles nodes that failed to respond within `ResumeTimeout`

### generate_conf.py

Configuration generator:

- Reads `config.json` and `partitions.json`
- Generates Slurm configuration in `slurm.conf.aws`
- Creates node and partition definitions

## Documentation

- [Configuration Reference](docs/configuration.md) - Detailed config.json and partitions.json documentation
- [Manual Installation Guide](docs/manual-installation.md) - Step-by-step installation instructions
- [Troubleshooting](docs/troubleshooting.md) - Common issues and solutions
- [Examples](examples/) - Sample configurations for common use cases

## Requirements

### Slurm
- Slurm with power save support (tested with 20.02.3)
- Headnode can be located anywhere (on-premises or AWS)

### Python
- Python 3.6 or higher
- boto3 library
- AWS CLI (for credential configuration)

### AWS
- VPC with subnets for compute nodes
- EC2 launch template(s)
- IAM roles for headnode and compute nodes
- Private connectivity if headnode is off-AWS (VPN, Direct Connect, etc.)

### Compute Nodes
- Must retrieve cluster name from EC2 instance tag
- Instance metadata tags must be enabled

See [Manual Installation Guide](docs/manual-installation.md) for detailed prerequisites.

## IAM Permissions

### Headnode Permissions

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

### Compute Node Permissions

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

## Monitoring and Operations

### Logs

- Plugin logs: Check `LogFileName` configured in `config.json` (default: `aws_plugin.log`)
- Slurm logs: Standard slurmctld logs

### Monitoring Commands

```bash
# View partition status
sinfo

# View node details
scontrol show nodes

# View queue
squeue

# View plugin logs
tail -f /var/log/slurm/aws.log
```

### Common Operations

```bash
# Reconfigure Slurm after config changes
scontrol reconfigure

# Manually drain a node
scontrol update NodeName=aws-node-0 State=DRAIN Reason="maintenance"

# Resume a drained node
scontrol update NodeName=aws-node-0 State=RESUME
```

## Troubleshooting

See the [Troubleshooting Guide](docs/troubleshooting.md) for solutions to common problems.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

This project is licensed under the Apache License 2.0. See the [LICENSE](LICENSE) file for details.

Original work Copyright 2020 Amazon.com, Inc. or its affiliates (MIT-0 License)
Modified work Copyright 2025 Scott Friedman (Apache License 2.0)

## Support

This is a sample project provided as-is. For issues and questions:

- Check the [Troubleshooting Guide](docs/troubleshooting.md)
- Review [Slurm documentation](https://slurm.schedmd.com/documentation.html)
- Open an issue on GitHub

## Related Projects

- [AWS ParallelCluster](https://aws.amazon.com/hpc/parallelcluster/) - Managed HPC cluster solution
- [Slurm Documentation](https://slurm.schedmd.com/) - Official Slurm documentation
- [EC2 Fleet](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-fleet.html) - AWS EC2 Fleet documentation
