# CloudFormation Deployment Guide

This guide provides detailed information about deploying the AWS Plugin for Slurm using AWS CloudFormation.

## Overview

The CloudFormation template (`template.yaml`) deploys a fully functional Slurm headnode with the plugin pre-configured. This is the fastest way to test the plugin or deploy a proof-of-concept HPC cluster.

## What Gets Deployed

The CloudFormation stack creates:

### Infrastructure Resources
- **Security Group** - Controls network access
  - Allows SSH (port 22) from the internet
  - Allows all traffic between Slurm nodes (headnode and compute nodes)
- **Network Interface** - Dedicated network interface for the headnode with static private IP

### IAM Resources
- **Headnode IAM Role** - Grants permissions to:
  - Launch and terminate EC2 instances
  - Create EC2 Fleets
  - Tag instances
  - Describe instances
  - Create service-linked roles for EC2 Fleet
  - Pass the compute node IAM role
  - SSM Session Manager access (optional)

- **Compute Node IAM Role** - Grants permissions to:
  - Describe instance tags (for retrieving node name)
  - SSM Session Manager access (optional)

### EC2 Resources
- **Launch Template** - Defines compute node configuration
  - Uses latest Amazon Linux 2 AMI
  - Enables instance metadata tags (IMDSv2)
  - Configures Munge authentication
  - Mounts NFS share from headnode
  - Automatically starts slurmd with correct node name

- **Headnode Instance** - EC2 instance that runs:
  - Slurm controller (slurmctld)
  - NFS server (shares Slurm installation with compute nodes)
  - Munge authentication daemon
  - Plugin scripts and configuration

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────┐
│                      VPC (User-provided)                 │
│                                                          │
│  ┌────────────────────────────────────────────────────┐ │
│  │  Subnet 1                                          │ │
│  │                                                    │ │
│  │  ┌──────────────────────┐                         │ │
│  │  │   Headnode           │                         │ │
│  │  │  - slurmctld         │                         │ │
│  │  │  - NFS Server        │                         │ │
│  │  │  - Munge             │                         │ │
│  │  │  - Plugin Scripts    │                         │ │
│  │  └──────────┬───────────┘                         │ │
│  │             │ NFS                                  │ │
│  │             │                                      │ │
│  │  ┌──────────▼───────────┐  ┌──────────────────┐  │ │
│  │  │  Compute Node 1      │  │  Compute Node 2  │  │ │
│  │  │  - slurmd            │  │  - slurmd        │  │ │
│  │  │  - Munge             │  │  - Munge         │  │ │
│  │  └──────────────────────┘  └──────────────────┘  │ │
│  └────────────────────────────────────────────────────┘ │
│                                                          │
│  ┌────────────────────────────────────────────────────┐ │
│  │  Subnet 2                                          │ │
│  │                                                    │ │
│  │  ┌──────────────────────┐  ┌──────────────────┐  │ │
│  │  │  Compute Node 3      │  │  Compute Node 4  │  │ │
│  │  │  - slurmd            │  │  - slurmd        │  │ │
│  │  │  - Munge             │  │  - Munge         │  │ │
│  │  └──────────────────────┘  └──────────────────┘  │ │
│  └────────────────────────────────────────────────────┘ │
│                                                          │
└─────────────────────────────────────────────────────────┘
                         │
                         │ Internet Gateway
                         │
                    ┌────▼────┐
                    │ Internet │
                    └─────────┘
```

## Prerequisites

### Required Resources
- **AWS Account** with permissions to create:
  - EC2 instances, security groups, network interfaces
  - IAM roles and policies
  - CloudFormation stacks

- **VPC** with:
  - Internet Gateway attached
  - Two subnets in different Availability Zones
  - Subnets must be public OR private with NAT Gateway

- **SSH Key Pair** (recommended for access)

### Network Requirements

**Subnets must have:**
- Internet access (direct or via NAT Gateway) for:
  - Downloading Slurm source code
  - Installing packages via yum
  - Accessing AWS APIs

**Recommended topology:**
- Public subnets with Internet Gateway
- OR Private subnets with NAT Gateway in each AZ

## Parameters

### Network Parameters

#### VpcId
- **Type**: VPC ID
- **Description**: Existing VPC where resources will be launched
- **Example**: `vpc-0123456789abcdef0`

#### Subnet1Id
- **Type**: Subnet ID
- **Description**: First subnet for headnode and compute nodes
- **Requirements**:
  - Must be in the VPC specified
  - Must have route to internet (direct or via NAT)
  - If public, must have auto-assign public IP enabled

#### Subnet2Id
- **Type**: Subnet ID
- **Description**: Second subnet for compute nodes (diversification)
- **Requirements**:
  - Must be in the VPC specified
  - Must be in a **different AZ** than Subnet1
  - Must have route to internet (direct or via NAT)

### Instance Parameters

#### HeadNodeInstanceType
- **Type**: String
- **Default**: `c5.large`
- **Description**: Instance type for the headnode
- **Recommendations**:
  - Small clusters: `t3.medium` or `c5.large`
  - Medium clusters: `c5.xlarge` or `c5.2xlarge`
  - Large clusters: `c5.4xlarge` or `m5.4xlarge`

#### ComputeNodeInstanceType
- **Type**: String
- **Default**: `c5.large`
- **Description**: Instance type for compute nodes
- **Note**: Should match workload requirements

#### ComputeNodeCPUs
- **Type**: Number
- **Default**: `2`
- **Description**: Number of vCPUs for the compute node instance type
- **Important**: Must match the actual vCPU count of the instance type

#### KeyPair
- **Type**: EC2 KeyPair name
- **Description**: SSH key pair for instance access
- **Recommended**: Always specify for troubleshooting access

#### LatestAmiId
- **Type**: SSM Parameter
- **Default**: Latest Amazon Linux 2 AMI
- **Description**: AMI ID to use for instances
- **Note**: Uses AWS Systems Manager parameter store to automatically fetch latest AL2 AMI

### Package Parameters

#### SlurmPackageUrl
- **Type**: URL
- **Default**: `https://download.schedmd.com/slurm/slurm-21.08-latest.tar.bz2`
- **Description**: URL to Slurm source tarball
- **Format**: Must be `slurm-*.tar.bz2`
- **Customization**: Can point to specific Slurm version

#### PluginPrefixUrl
- **Type**: URL
- **Default**: `https://github.com/aws-samples/aws-plugin-for-slurm/raw/plugin-v2/`
- **Description**: Base URL for plugin files
- **Note**: Should be updated to `plugin-v3` for this version

## Deployment Steps

### 1. Create the Stack

#### Via AWS Console

1. Navigate to CloudFormation in AWS Console
2. Click "Create stack" → "With new resources"
3. Choose "Upload a template file"
4. Upload `template.yaml`
5. Click "Next"

#### Via AWS CLI

```bash
aws cloudformation create-stack \
  --stack-name slurm-cluster \
  --template-body file://template.yaml \
  --parameters \
    ParameterKey=VpcId,ParameterValue=vpc-xxxxx \
    ParameterKey=Subnet1Id,ParameterValue=subnet-xxxxx \
    ParameterKey=Subnet2Id,ParameterValue=subnet-yyyyy \
    ParameterKey=KeyPair,ParameterValue=my-keypair \
  --capabilities CAPABILITY_IAM
```

### 2. Monitor Stack Creation

The stack takes approximately 10-15 minutes to create.

**Monitor progress:**
```bash
aws cloudformation describe-stacks \
  --stack-name slurm-cluster \
  --query 'Stacks[0].StackStatus'
```

**Watch events:**
```bash
aws cloudformation describe-stack-events \
  --stack-name slurm-cluster
```

### 3. Retrieve Headnode Instance ID

After stack creation completes:

```bash
aws cloudformation describe-stacks \
  --stack-name slurm-cluster \
  --query 'Stacks[0].Outputs[?OutputKey==`HeadNodeId`].OutputValue' \
  --output text
```

### 4. Connect to Headnode

Using EC2 Instance Connect or SSH:

```bash
# Get public IP
aws ec2 describe-instances \
  --instance-ids i-xxxxx \
  --query 'Reservations[0].Instances[0].PublicIpAddress' \
  --output text

# SSH
ssh -i ~/.ssh/my-keypair.pem ec2-user@<public-ip>
```

Or use Session Manager (no SSH key required):
```bash
aws ssm start-session --target i-xxxxx
```

## Post-Deployment Configuration

### Default Configuration

The stack creates a single partition called `aws` with:
- **Partition Name**: `aws`
- **Node Group**: `node`
- **Max Nodes**: 100
- **Instance Type**: As specified in parameters
- **Purchasing**: On-demand
- **Subnets**: Both Subnet1 and Subnet2

### Customizing the Configuration

After deployment, you can modify the configuration:

1. **SSH to headnode**

2. **Edit configuration files:**
   ```bash
   cd /nfs/slurm/etc/aws
   vi config.json       # Adjust plugin settings
   vi partitions.json   # Modify node groups/partitions
   ```

3. **Regenerate Slurm configuration:**
   ```bash
   ./generate_conf.py
   ```

4. **Update slurm.conf:**
   ```bash
   # Remove old AWS config
   sed -i '/# AWS Plugin Configuration/,$d' /nfs/slurm/etc/slurm.conf

   # Append new config
   cat slurm.conf.aws >> /nfs/slurm/etc/slurm.conf
   ```

5. **Reconfigure Slurm:**
   ```bash
   scontrol reconfigure
   ```

## Testing the Deployment

### 1. Check Slurm Status

```bash
sinfo
```

Expected output:
```
PARTITION AVAIL  TIMELIMIT  NODES  STATE NODELIST
aws*         up   infinite    100  idle~ aws-node-[0-99]
```

### 2. Submit a Test Job

```bash
srun -N1 hostname
```

This should:
- Launch an EC2 instance
- Register it with Slurm
- Run the command
- Return the hostname

### 3. Monitor Instance Launch

In another terminal:
```bash
watch 'sinfo -N'
```

You should see the node transition through states:
- `idle~` → `alloc#` → `alloc` → `idle` → `idle~`

### 4. Check EC2 Console

Verify the instance appears in EC2 console with:
- Name tag: `aws-node-0`
- State: Running (while job is active)
- After `SuspendTime` seconds: Terminated

## Architecture Details

### NFS Configuration

The headnode runs an NFS server that shares `/nfs` with compute nodes:

- **Exports**: `/nfs` with `rw,async,no_root_squash`
- **Mount Point**: Compute nodes mount to `/nfs`
- **Contents**: Slurm binaries, configuration, and plugin scripts

**Why NFS?**
- Ensures all nodes use identical Slurm binaries
- Simplifies configuration management
- Allows compute nodes to be stateless

### Munge Configuration

Munge provides authentication between Slurm nodes:

- **Key**: Hard-coded in template (CHANGE FOR PRODUCTION)
- **Key Location**: `/etc/munge/munge.key`
- **Permissions**: `600`, owned by `munge:munge`

**Security Warning**: The template uses a hard-coded Munge key for demonstration. In production:
1. Generate a unique key: `dd if=/dev/urandom bs=1 count=1024 > munge.key`
2. Deploy via AWS Secrets Manager or Parameter Store
3. Never commit Munge keys to source control

### Compute Node Bootstrap

Compute nodes execute this sequence at launch:

1. **Install Packages**: Munge, NFS utilities
2. **Configure Munge**: Copy key from headnode (via userdata)
3. **Start Munge**: Enable and start munge daemon
4. **Mount NFS**: Mount `/nfs` from headnode
5. **Start slurmd**:
   - Retrieve node name from instance tag (IMDSv2)
   - Start slurmd with `-N $SLURM_NODENAME`

### IMDSv2 Node Name Retrieval

The template includes a script (`get_nodename`) that uses IMDSv2:

```bash
# Get IMDSv2 token
TOKEN=$(curl -X PUT "http://169.254.169.254/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" -s)

# Get Name tag
NAME_TAG=$(curl -H "X-aws-ec2-metadata-token: $TOKEN" \
    -s "http://169.254.169.254/latest/meta-data/tags/instance/Name")
```

This is more secure than IMDSv1 as it prevents SSRF attacks.

## Cost Considerations

### Headnode Costs
- Runs continuously
- `c5.large` costs ~$0.085/hour (~$62/month)
- Plus EBS volume (~$0.10/GB/month for gp2)

### Compute Node Costs
- Pay only when running
- Automatically terminated after `SuspendTime` (350 seconds by default)
- `c5.large` costs ~$0.085/hour

### Example Monthly Cost

**Light usage** (headnode + 10 hours of 1 compute node):
- Headnode: $62
- Compute: $0.85
- **Total**: ~$63/month

**Moderate usage** (headnode + 100 hours of 5 compute nodes):
- Headnode: $62
- Compute: $42.50
- **Total**: ~$105/month

### Cost Optimization Tips

1. **Use Spot instances** - Modify `partitions.json` to use Spot
2. **Stop headnode** when not in use (requires restart setup)
3. **Use smaller headnode** - `t3.medium` for small clusters
4. **Short SuspendTime** - Minimize idle compute node time
5. **Reserved Instances** - For headnode if running 24/7

## Troubleshooting

### Stack Creation Fails

**Check CloudFormation events:**
```bash
aws cloudformation describe-stack-events \
  --stack-name slurm-cluster \
  --max-items 20
```

**Common issues:**
- Insufficient permissions
- VPC/subnet doesn't exist
- Subnets in same AZ
- No internet access from subnets

### Headnode Bootstrap Fails

**Check CloudWatch Logs:**
```bash
aws logs tail /aws/ec2/userData/<instance-id>
```

**SSH to headnode and check:**
```bash
# Check if Slurm compiled
ls -la /nfs/slurm

# Check slurmctld status
systemctl status slurmctld

# Check logs
tail -f /var/log/slurmctld.log
```

### Compute Nodes Don't Launch

**Check plugin logs on headnode:**
```bash
tail -f /var/log/slurm_plugin.log
```

**Common issues:**
- IAM permissions missing
- Launch template invalid
- Subnet capacity exhausted
- Instance type not available in AZ

### Nodes Stuck in Power-Up

**Check:**
- Network connectivity between headnode and compute nodes
- Security group allows Slurm ports
- NFS mount successful on compute nodes
- Munge running on both nodes

**SSH to stuck compute node:**
```bash
# Check slurmd
systemctl status slurmd
journalctl -u slurmd

# Check NFS
mount | grep nfs
ls /nfs/slurm

# Check Munge
systemctl status munge
munge -n | unmunge
```

## Updating the Stack

To update the stack (e.g., change instance types):

```bash
aws cloudformation update-stack \
  --stack-name slurm-cluster \
  --use-previous-template \
  --parameters \
    ParameterKey=ComputeNodeInstanceType,ParameterValue=c5.xlarge \
  --capabilities CAPABILITY_IAM
```

**Note**: Changes to headnode instance type require replacement (downtime).

## Deleting the Stack

```bash
aws cloudformation delete-stack --stack-name slurm-cluster
```

**What gets deleted:**
- Headnode instance
- Security group
- IAM roles
- Launch template
- Network interface

**What persists:**
- Any running compute nodes (terminate manually)
- EBS volumes with delete-on-termination=false
- CloudWatch logs

**Clean up compute nodes:**
```bash
# Find and terminate any remaining compute nodes
aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=aws-node-*" \
            "Name=instance-state-name,Values=running" \
  --query 'Reservations[].Instances[].InstanceId' \
  --output text | xargs -r aws ec2 terminate-instances --instance-ids
```

## Production Considerations

### Security Hardening

1. **Generate unique Munge key**
2. **Restrict security group** - Limit SSH to your IP
3. **Use private subnets** for compute nodes
4. **Enable CloudTrail** for audit logging
5. **Use Secrets Manager** for sensitive data
6. **Enable VPC Flow Logs**

### High Availability

1. **Headnode backups** - Snapshot EBS volumes
2. **Multi-AZ** - Already configured for compute nodes
3. **Auto-recovery** - Enable EC2 auto-recovery for headnode
4. **State preservation** - Back up `/var/spool/slurm`

### Monitoring

1. **CloudWatch Metrics** - Enable detailed monitoring
2. **CloudWatch Logs** - Ship Slurm logs to CloudWatch
3. **CloudWatch Alarms** - Alert on headnode issues
4. **Cost Monitoring** - Enable AWS Cost Explorer

### Compliance

1. **Encryption** - Enable EBS encryption
2. **IMDSv2** - Already enforced in template
3. **SSM** - Enabled for Session Manager access
4. **Tagging** - Add cost allocation tags

## Next Steps

- Review [Security Best Practices](security.md)
- Configure [Monitoring](monitoring.md)
- Explore [Advanced Usage](advanced-usage.md)
- Set up [Performance Tuning](performance-tuning.md)
