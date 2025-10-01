# On-Premises to AWS Cloud Bursting Guide

This guide walks you through setting up cloud bursting from your **on-premises Slurm cluster** to AWS. This is the primary use case for this plugin.

## Overview

Cloud bursting allows your on-premises HPC cluster to dynamically provision additional compute capacity in AWS when your local resources are fully utilized. This gives you:

- **Overflow capacity** - Handle workload spikes without buying more hardware
- **Access to specialized hardware** - GPU instances, high-memory instances, etc.
- **Cost efficiency** - Pay only for what you use, when you use it

## Architecture

```
┌─────────────────────────────────────────┐         ┌──────────────────────────────────┐
│  On-Premises Data Center                 │         │  AWS VPC (us-east-1)             │
│                                          │         │                                  │
│  ┌────────────────────────┐              │         │  ┌────────────────────────┐     │
│  │  Slurm Headnode        │              │         │  │  Burst Compute Node 1  │     │
│  │  10.0.1.100            │◄─────────────┼─────────┼─►│  10.1.1.50            │     │
│  │                        │   VPN/Direct │         │  │                        │     │
│  │  - slurmctld           │   Connect    │         │  │  - slurmd              │     │
│  │  - NFS Server          │              │         │  │  - Munge               │     │
│  │  - Plugin Scripts      │              │         │  │  - NFS client          │     │
│  │  - Munge               │              │         │  └────────────────────────┘     │
│  └────────────────────────┘              │         │                                  │
│                                          │         │  ┌────────────────────────┐     │
│  ┌────────────────────────┐              │         │  │  Burst Compute Node 2  │     │
│  │  Static Compute Nodes  │              │         │  │  10.1.1.51            │     │
│  │  (Your existing nodes) │              │         │  │                        │     │
│  │  10.0.1.[10-50]        │              │         │  │  - slurmd              │     │
│  └────────────────────────┘              │         │  │  - Munge               │     │
│                                          │         │  │  - NFS client          │     │
└─────────────────────────────────────────┘         │  └────────────────────────┘     │
                                                     │                                  │
                                                     │  Subnet: 10.1.1.0/24             │
                                                     │  Private connectivity only       │
                                                     └──────────────────────────────────┘
```

**Key Points:**
- Your headnode stays on-premises (no changes to existing cluster)
- AWS compute nodes mount NFS from on-prem headnode
- Plugin runs on on-prem headnode, launches instances in AWS
- No DNS required - plugin injects IPs directly into Slurm
- Munge authentication works across the VPN

## Prerequisites

### 1. Working On-Premises Slurm Cluster

You must have:
- Slurm headnode with slurmctld running
- Slurm version 20.02.3+ with power save mode support
- NFS server sharing Slurm installation
- Munge authentication configured
- Root or slurm user access to headnode

**Verify your Slurm version:**
```bash
slurmctld --version
# Example output: slurm 20.02.3
```

**Critical**: Write down your exact Slurm version. Your AWS AMI **must** use the same version.

### 2. Network Connectivity to AWS

You need private network connectivity between your on-prem network and an AWS VPC. Choose one:

#### Option A: AWS Site-to-Site VPN (Easiest)
- Setup time: 30 minutes
- Cost: ~$36/month + data transfer
- Bandwidth: Up to 1.25 Gbps per tunnel
- Latency: Internet-based (varies)

**Good for:**
- Testing/proof of concept
- Light workloads
- Budget-conscious deployments

#### Option B: AWS Direct Connect (Production)
- Setup time: Days to weeks
- Cost: Port fee + data transfer
- Bandwidth: 1 Gbps to 100 Gbps
- Latency: Dedicated fiber (predictable)

**Good for:**
- Production workloads
- Heavy data transfer
- Latency-sensitive applications
- Long-term deployments

**For this guide, we'll use Site-to-Site VPN** as it's faster to set up. The configuration is identical once connectivity is established.

### 3. AWS Account

You need:
- AWS account with EC2 access
- IAM permissions to create roles, VPCs, VPNs
- VPC with private subnets for compute nodes
- Budget for compute instances

## Step-by-Step Setup

### Step 1: Establish Network Connectivity

#### 1A. Create AWS VPC and Subnets

```bash
# Set your region
export AWS_REGION=us-east-1

# Create VPC (10.1.0.0/16 - adjust if conflicts with on-prem)
VPC_ID=$(aws ec2 create-vpc \
  --cidr-block 10.1.0.0/16 \
  --region $AWS_REGION \
  --tag-specifications 'ResourceType=vpc,Tags=[{Key=Name,Value=slurm-burst-vpc}]' \
  --query 'Vpc.VpcId' \
  --output text)

echo "VPC ID: $VPC_ID"

# Create subnet for compute nodes (10.1.1.0/24)
SUBNET_ID=$(aws ec2 create-subnet \
  --vpc-id $VPC_ID \
  --cidr-block 10.1.1.0/24 \
  --availability-zone ${AWS_REGION}a \
  --tag-specifications 'ResourceType=subnet,Tags=[{Key=Name,Value=slurm-compute-subnet}]' \
  --query 'Subnet.SubnetId' \
  --output text)

echo "Subnet ID: $SUBNET_ID"

# Enable private DNS hostnames
aws ec2 modify-vpc-attribute \
  --vpc-id $VPC_ID \
  --enable-dns-hostnames

aws ec2 modify-vpc-attribute \
  --vpc-id $VPC_ID \
  --enable-dns-support
```

#### 1B. Set Up Site-to-Site VPN

**On AWS side:**

```bash
# 1. Create Customer Gateway (your on-prem router's public IP)
ONPREM_PUBLIC_IP="203.0.113.50"  # Replace with your public IP

CGW_ID=$(aws ec2 create-customer-gateway \
  --type ipsec.1 \
  --public-ip $ONPREM_PUBLIC_IP \
  --bgp-asn 65000 \
  --tag-specifications 'ResourceType=customer-gateway,Tags=[{Key=Name,Value=onprem-gateway}]' \
  --query 'CustomerGateway.CustomerGatewayId' \
  --output text)

echo "Customer Gateway ID: $CGW_ID"

# 2. Create Virtual Private Gateway
VGW_ID=$(aws ec2 create-vpn-gateway \
  --type ipsec.1 \
  --tag-specifications 'ResourceType=vpn-gateway,Tags=[{Key=Name,Value=slurm-vpn-gateway}]' \
  --query 'VpnGateway.VpnGatewayId' \
  --output text)

echo "Virtual Private Gateway ID: $VGW_ID"

# 3. Attach VGW to VPC
aws ec2 attach-vpn-gateway \
  --vpc-id $VPC_ID \
  --vpn-gateway-id $VGW_ID

# 4. Create VPN Connection
VPN_ID=$(aws ec2 create-vpn-connection \
  --type ipsec.1 \
  --customer-gateway-id $CGW_ID \
  --vpn-gateway-id $VGW_ID \
  --options TunnelOptions=[{PreSharedKey=YourStrongPSK123456789}] \
  --tag-specifications 'ResourceType=vpn-connection,Tags=[{Key=Name,Value=slurm-vpn}]' \
  --query 'VpnConnection.VpnConnectionId' \
  --output text)

echo "VPN Connection ID: $VPN_ID"

# 5. Download VPN configuration
aws ec2 describe-vpn-connections \
  --vpn-connection-ids $VPN_ID \
  --query 'VpnConnections[0].CustomerGatewayConfiguration' \
  --output text > vpn-config.xml

echo "VPN configuration saved to vpn-config.xml"
```

**On your on-premises side:**

You need to configure your router/firewall with the VPN details from `vpn-config.xml`. The specific steps depend on your equipment:

- **pfSense**: Use IPsec VPN wizard
- **Cisco**: `crypto map` configuration
- **Fortinet**: IPsec VPN tunnel setup
- **Linux strongSwan**: `/etc/ipsec.conf` and `/etc/ipsec.secrets`

**Example for strongSwan (Linux):**

```bash
# Install strongSwan
sudo yum install -y strongswan

# Extract tunnel IPs from vpn-config.xml
AWS_TUNNEL1_IP="52.1.2.3"     # Get from vpn-config.xml
AWS_TUNNEL2_IP="52.4.5.6"     # Get from vpn-config.xml
PRESHARED_KEY="YourStrongPSK123456789"

# Configure /etc/strongswan/ipsec.conf
sudo tee /etc/strongswan/ipsec.conf > /dev/null <<EOF
config setup
    charondebug="ike 2, knl 2, cfg 2"

conn aws-tunnel1
    auto=start
    left=%defaultroute
    leftid=$ONPREM_PUBLIC_IP
    right=$AWS_TUNNEL1_IP
    type=tunnel
    ikelifetime=8h
    keylife=1h
    rekeymargin=3m
    keyingtries=3
    authby=secret
    keyexchange=ikev1
    ike=aes128-sha1-modp1024
    esp=aes128-sha1-modp1024
    leftsubnet=10.0.1.0/24
    rightsubnet=10.1.0.0/16
EOF

# Configure pre-shared key
sudo tee /etc/strongswan/ipsec.secrets > /dev/null <<EOF
$ONPREM_PUBLIC_IP $AWS_TUNNEL1_IP : PSK "$PRESHARED_KEY"
EOF

# Start VPN
sudo systemctl enable strongswan
sudo systemctl start strongswan
```

#### 1C. Configure Routing

**On AWS side:**

```bash
# Enable route propagation from VGW
ROUTE_TABLE_ID=$(aws ec2 describe-route-tables \
  --filters "Name=vpc-id,Values=$VPC_ID" \
  --query 'RouteTables[0].RouteTableId' \
  --output text)

aws ec2 enable-vgw-route-propagation \
  --route-table-id $ROUTE_TABLE_ID \
  --gateway-id $VGW_ID

# Verify routes are propagating
aws ec2 describe-route-tables \
  --route-table-ids $ROUTE_TABLE_ID \
  --query 'RouteTables[0].Routes'
```

**On on-prem side:**

Add route to AWS VPC CIDR through the VPN tunnel:

```bash
# Add route to AWS VPC (adjust interface as needed)
sudo ip route add 10.1.0.0/16 via 169.254.0.1 dev ipsec0
# Or configure in your router's routing table
```

#### 1D. Test Connectivity

**From your on-prem headnode:**

```bash
# Test ping to AWS subnet (need a test instance first)
# Launch a temporary test instance
TEST_INSTANCE=$(aws ec2 run-instances \
  --image-id ami-0c55b159cbfafe1f0 \
  --instance-type t3.micro \
  --subnet-id $SUBNET_ID \
  --query 'Instances[0].InstanceId' \
  --output text)

# Wait for it to start
aws ec2 wait instance-running --instance-ids $TEST_INSTANCE

# Get its private IP
TEST_IP=$(aws ec2 describe-instances \
  --instance-ids $TEST_INSTANCE \
  --query 'Reservations[0].Instances[0].PrivateIpAddress' \
  --output text)

echo "Test instance IP: $TEST_IP"

# From on-prem headnode, ping it
ping -c 4 $TEST_IP

# If ping works, test NFS connectivity
showmount -e $TEST_IP
```

**If connectivity fails:**
- Check VPN tunnel status: `aws ec2 describe-vpn-connections --vpn-connection-ids $VPN_ID`
- Verify security groups allow ICMP and your traffic
- Check routing tables on both sides
- Verify firewall rules on on-prem network

### Step 2: Build AWS AMI with Matching Slurm Version

This is the **most critical step**. Your AMI must have Slurm installed at the exact same version as your on-prem cluster.

#### 2A. Launch Base Instance for AMI Building

```bash
# Use Amazon Linux 2 as base
BASE_AMI=$(aws ec2 describe-images \
  --owners amazon \
  --filters "Name=name,Values=amzn2-ami-hvm-*-x86_64-gp2" \
            "Name=state,Values=available" \
  --query 'Images | sort_by(@, &CreationDate) | [-1].ImageId' \
  --output text)

# Launch in your AWS subnet
BUILD_INSTANCE=$(aws ec2 run-instances \
  --image-id $BASE_AMI \
  --instance-type t3.medium \
  --subnet-id $SUBNET_ID \
  --key-name YOUR_KEY_PAIR \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=slurm-ami-builder}]' \
  --query 'Instances[0].InstanceId' \
  --output text)

echo "Build instance: $BUILD_INSTANCE"

# Get its IP
BUILD_IP=$(aws ec2 describe-instances \
  --instance-ids $BUILD_INSTANCE \
  --query 'Reservations[0].Instances[0].PrivateIpAddress' \
  --output text)

echo "Connect via: ssh -i your-key.pem ec2-user@$BUILD_IP"
```

#### 2B. Build Slurm on AMI

**SSH into the build instance and run:**

```bash
# Get your exact Slurm version from on-prem
SLURM_VERSION="20.02.3"  # Replace with output from: slurmctld --version

# Install dependencies
sudo yum install -y \
  munge munge-libs munge-devel \
  openssl openssl-devel \
  pam-devel \
  numactl numactl-devel \
  hwloc hwloc-devel \
  lua lua-devel \
  readline-devel \
  rrdtool-devel \
  ncurses-devel \
  man2html \
  libibmad \
  libibumad \
  rpm-build \
  perl \
  gcc \
  make \
  nfs-utils \
  chrony

# Download exact Slurm version
cd /tmp
wget https://download.schedmd.com/slurm/slurm-${SLURM_VERSION}.tar.bz2
tar xjf slurm-${SLURM_VERSION}.tar.bz2
cd slurm-${SLURM_VERSION}

# Configure Slurm (match your on-prem prefix)
# Check your on-prem: which slurmctld  (e.g., /usr/local/bin or /nfs/slurm/bin)
SLURM_PREFIX="/nfs/slurm"  # Adjust to match your on-prem path

./configure --prefix=$SLURM_PREFIX
make -j$(nproc)
sudo make install

# Verify version
$SLURM_PREFIX/sbin/slurmd --version
# Should match your on-prem exactly

# Create Slurm user (match on-prem UID if possible)
sudo groupadd -g 1001 slurm
sudo useradd -u 1001 -g slurm -s /bin/bash slurm
```

#### 2C. Configure NFS Client

```bash
# Test mounting NFS from on-prem headnode
HEADNODE_IP="10.0.1.100"  # Your on-prem headnode IP
NFS_EXPORT="/nfs"          # Your NFS export path

# Test mount
sudo mkdir -p /nfs
sudo mount -t nfs ${HEADNODE_IP}:${NFS_EXPORT} /nfs

# Verify Slurm installation is accessible
ls -la /nfs/slurm/sbin/slurmd

# If successful, configure auto-mount in /etc/fstab
echo "${HEADNODE_IP}:${NFS_EXPORT}  /nfs  nfs  defaults,_netdev  0 0" | sudo tee -a /etc/fstab

# Unmount for now (will auto-mount on boot)
sudo umount /nfs
```

#### 2D. Configure Munge

**Copy Munge key from on-prem headnode to AWS:**

```bash
# On your on-prem headnode:
# Option 1: Upload to S3
aws s3 cp /etc/munge/munge.key s3://YOUR-BUCKET/slurm/munge.key

# Option 2: Upload to AWS Secrets Manager (more secure)
aws secretsmanager create-secret \
  --name slurm/munge-key \
  --description "Munge key for Slurm cluster" \
  --secret-binary fileb:///etc/munge/munge.key \
  --region $AWS_REGION
```

**On the AMI build instance:**

```bash
# Retrieve Munge key
# Option 1: From S3
aws s3 cp s3://YOUR-BUCKET/slurm/munge.key /tmp/munge.key

# Option 2: From Secrets Manager (requires IAM permission)
aws secretsmanager get-secret-value \
  --secret-id slurm/munge-key \
  --region $AWS_REGION \
  --query SecretBinary \
  --output text | base64 -d > /tmp/munge.key

# Install Munge key
sudo mv /tmp/munge.key /etc/munge/munge.key
sudo chown munge:munge /etc/munge/munge.key
sudo chmod 600 /etc/munge/munge.key
sudo chown -R munge:munge /etc/munge/ /var/log/munge/ /var/lib/munge/
sudo chmod 0700 /etc/munge/ /var/log/munge/ /var/lib/munge/

# Configure Munge to start on boot
sudo systemctl enable munge

# Test (will fail until headnode is reachable and key is correct)
sudo systemctl start munge
munge -n | unmunge
```

#### 2E. Configure Slurmd Service with IMDSv2

```bash
# Create script to get node name from EC2 tag
sudo tee /usr/local/bin/get_slurm_nodename > /dev/null <<'EOF'
#!/bin/bash

# Get IMDSv2 token
TOKEN=$(curl -X PUT "http://169.254.169.254/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" \
    -s --max-time 2)

if [ -z "$TOKEN" ]; then
    echo "Error: Failed to retrieve IMDSv2 token"
    echo "$(hostname)"
    exit 0
fi

# Get Name tag from instance metadata
NAME_TAG=$(curl -H "X-aws-ec2-metadata-token: $TOKEN" \
    -s --max-time 2 \
    "http://169.254.169.254/latest/meta-data/tags/instance/Name")

if [ -z "$NAME_TAG" ] || [ "$NAME_TAG" = "None" ]; then
    echo "$(hostname)"
else
    echo "$NAME_TAG"
fi
EOF

sudo chmod +x /usr/local/bin/get_slurm_nodename

# Create slurmd systemd service
sudo tee /etc/systemd/system/slurmd.service > /dev/null <<'EOF'
[Unit]
Description=Slurm node daemon
After=munge.service network.target remote-fs.target nfs.target
Requires=munge.service

[Service]
Type=forking
EnvironmentFile=-/etc/sysconfig/slurmd
ExecStartPre=/bin/bash -c "/bin/systemctl set-environment SLURM_NODENAME=$(/usr/local/bin/get_slurm_nodename)"
ExecStart=/nfs/slurm/sbin/slurmd -N $SLURM_NODENAME $SLURMD_OPTIONS
ExecReload=/bin/kill -HUP $MAINPID
PIDFile=/var/run/slurmd.pid
KillMode=process
LimitNOFILE=131072
LimitMEMLOCK=infinity
LimitSTACK=infinity
Delegate=yes

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable slurmd
```

#### 2F. Create AMI from Build Instance

```bash
# Stop the build instance (required for AMI creation)
aws ec2 stop-instances --instance-ids $BUILD_INSTANCE
aws ec2 wait instance-stopped --instance-ids $BUILD_INSTANCE

# Create AMI
SLURM_AMI=$(aws ec2 create-image \
  --instance-id $BUILD_INSTANCE \
  --name "slurm-compute-node-${SLURM_VERSION}-$(date +%Y%m%d)" \
  --description "Slurm ${SLURM_VERSION} compute node for cloud bursting" \
  --query 'ImageId' \
  --output text)

echo "AMI ID: $SLURM_AMI"
echo "Save this - you'll use it in your launch template"

# Wait for AMI to be available
aws ec2 wait image-available --image-ids $SLURM_AMI
echo "AMI is ready!"

# Terminate build instance (optional)
# aws ec2 terminate-instances --instance-ids $BUILD_INSTANCE
```

**Congratulations!** You now have an AMI with Slurm that matches your on-prem cluster.

### Step 3: Configure AWS Resources for Compute Nodes

#### 3A. Create IAM Role for Compute Nodes

```bash
# Trust policy
cat > /tmp/compute-trust-policy.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "ec2.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}
EOF

aws iam create-role \
  --role-name SlurmComputeNodeRole \
  --assume-role-policy-document file:///tmp/compute-trust-policy.json

# Permissions (needs to read tags + optionally get Munge key from Secrets Manager)
cat > /tmp/compute-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "ec2:DescribeTags",
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": "arn:aws:secretsmanager:${AWS_REGION}:$(aws sts get-caller-identity --query Account --output text):secret:slurm/munge-key-*"
    }
  ]
}
EOF

aws iam put-role-policy \
  --role-name SlurmComputeNodeRole \
  --policy-name ComputeNodePolicy \
  --policy-document file:///tmp/compute-policy.json

# Create instance profile
aws iam create-instance-profile \
  --instance-profile-name SlurmComputeNodeProfile

aws iam add-role-to-instance-profile \
  --instance-profile-name SlurmComputeNodeProfile \
  --role-name SlurmComputeNodeRole
```

#### 3B. Create Security Group

```bash
# Create security group
SG_ID=$(aws ec2 create-security-group \
  --group-name slurm-compute-nodes \
  --description "Security group for Slurm burst compute nodes" \
  --vpc-id $VPC_ID \
  --query 'GroupId' \
  --output text)

# Allow all traffic from on-prem network
ONPREM_CIDR="10.0.1.0/24"  # Your on-prem network CIDR
aws ec2 authorize-security-group-ingress \
  --group-id $SG_ID \
  --protocol all \
  --cidr $ONPREM_CIDR

# Allow all traffic within the security group (for multi-node jobs)
aws ec2 authorize-security-group-ingress \
  --group-id $SG_ID \
  --protocol all \
  --source-group $SG_ID

echo "Security Group ID: $SG_ID"
```

#### 3C. Create Launch Template

```bash
# Get instance profile ARN
INSTANCE_PROFILE_ARN=$(aws iam get-instance-profile \
  --instance-profile-name SlurmComputeNodeProfile \
  --query 'InstanceProfile.Arn' \
  --output text)

# Create launch template
aws ec2 create-launch-template \
  --launch-template-name slurm-burst-compute \
  --version-description "Slurm ${SLURM_VERSION} compute node for on-prem bursting" \
  --launch-template-data "{
    \"ImageId\": \"${SLURM_AMI}\",
    \"IamInstanceProfile\": {
      \"Arn\": \"${INSTANCE_PROFILE_ARN}\"
    },
    \"SecurityGroupIds\": [\"${SG_ID}\"],
    \"MetadataOptions\": {
      \"HttpTokens\": \"required\",
      \"HttpPutResponseHopLimit\": 1,
      \"InstanceMetadataTags\": \"enabled\"
    },
    \"TagSpecifications\": [{
      \"ResourceType\": \"instance\",
      \"Tags\": [
        {\"Key\": \"ManagedBy\", \"Value\": \"Slurm\"},
        {\"Key\": \"Environment\", \"Value\": \"CloudBurst\"}
      ]
    }]
  }"

# Get launch template ID
TEMPLATE_ID=$(aws ec2 describe-launch-templates \
  --launch-template-names slurm-burst-compute \
  --query 'LaunchTemplates[0].LaunchTemplateId' \
  --output text)

echo "Launch Template ID: $TEMPLATE_ID"
```

### Step 4: Install and Configure Plugin on On-Prem Headnode

#### 4A. Install Plugin Files

**On your on-prem headnode:**

```bash
# Create plugin directory
sudo mkdir -p /etc/slurm/aws-plugin
cd /etc/slurm/aws-plugin

# Download plugin files
sudo wget -q https://github.com/scttfrdmn/aws-plugin-for-slurm/raw/plugin-v3/common.py
sudo wget -q https://github.com/scttfrdmn/aws-plugin-for-slurm/raw/plugin-v3/resume.py
sudo wget -q https://github.com/scttfrdmn/aws-plugin-for-slurm/raw/plugin-v3/suspend.py
sudo wget -q https://github.com/scttfrdmn/aws-plugin-for-slurm/raw/plugin-v3/generate_conf.py
sudo wget -q https://github.com/scttfrdmn/aws-plugin-for-slurm/raw/plugin-v3/change_state.py
sudo chmod +x *.py

# Install Python dependencies
sudo yum install -y python3 python3-pip
sudo pip3 install boto3 awscli
```

#### 4B. Configure AWS Credentials

```bash
# Create IAM user for headnode (or use existing credentials)
# User needs: ec2:CreateFleet, ec2:RunInstances, ec2:TerminateInstances,
#             ec2:CreateTags, ec2:DescribeInstances, iam:PassRole

# Configure AWS CLI
aws configure set aws_access_key_id YOUR_ACCESS_KEY
aws configure set aws_secret_access_key YOUR_SECRET_KEY
aws configure set region $AWS_REGION

# Test
aws ec2 describe-instances --max-results 1
```

#### 4C. Create Configuration Files

```bash
# Create config.json
sudo tee /etc/slurm/aws-plugin/config.json > /dev/null <<EOF
{
  "LogLevel": "INFO",
  "LogFileName": "/var/log/slurm/aws_plugin.log",
  "SlurmBinPath": "/nfs/slurm/bin",
  "SlurmConf": {
    "PrivateData": "CLOUD",
    "ResumeProgram": "/etc/slurm/aws-plugin/resume.py",
    "SuspendProgram": "/etc/slurm/aws-plugin/suspend.py",
    "ResumeRate": 50,
    "SuspendRate": 50,
    "ResumeTimeout": 600,
    "SuspendTime": 900,
    "TreeWidth": 60000
  }
}
EOF

# Create partitions.json
sudo tee /etc/slurm/aws-plugin/partitions.json > /dev/null <<EOF
{
  "Partitions": [
    {
      "PartitionName": "cloud",
      "NodeGroups": [
        {
          "NodeGroupName": "burst",
          "MaxNodes": 50,
          "Region": "${AWS_REGION}",
          "SlurmSpecifications": {
            "CPUs": "4",
            "RealMemory": "15000",
            "Weight": "100"
          },
          "PurchasingOption": "on-demand",
          "OnDemandOptions": {
            "AllocationStrategy": "lowest-price"
          },
          "LaunchTemplateSpecification": {
            "LaunchTemplateId": "${TEMPLATE_ID}",
            "Version": "\$Latest"
          },
          "LaunchTemplateOverrides": [
            {
              "InstanceType": "c5.xlarge"
            }
          ],
          "SubnetIds": [
            "${SUBNET_ID}"
          ]
        }
      ],
      "PartitionOptions": {
        "Default": "No"
      }
    }
  ]
}
EOF
```

#### 4D. Generate and Apply Slurm Configuration

```bash
# Generate configuration
cd /etc/slurm/aws-plugin
sudo ./generate_conf.py

# Review generated config
cat slurm.conf.aws

# Append to your main slurm.conf
sudo cat slurm.conf.aws >> /etc/slurm/slurm.conf

# Reconfigure Slurm
sudo scontrol reconfigure

# Verify new partition exists
sinfo
# You should see 'cloud' partition with nodes in 'idle~' state
```

#### 4E. Set Up Cron Job

```bash
# Add cron job to manage node states
sudo crontab -e
# Add this line:
# * * * * * /etc/slurm/aws-plugin/change_state.py &>/dev/null
```

### Step 5: Test Cloud Bursting

#### 5A. Submit Test Job

```bash
# Submit job to cloud partition
srun -p cloud hostname

# Watch the magic happen:
# 1. Node changes from 'idle~' to 'alloc#' (powering up)
# 2. Plugin launches EC2 instance in AWS
# 3. Instance boots, mounts NFS, starts slurmd
# 4. Node changes to 'alloc' (ready)
# 5. Job runs
# 6. Node returns to 'idle' after job
# 7. After SuspendTime (900s), node returns to 'idle~' and instance terminates
```

#### 5B. Monitor Progress

```bash
# Watch Slurm nodes
watch sinfo -p cloud

# Watch plugin logs
tail -f /var/log/slurm/aws_plugin.log

# Watch AWS instances
watch 'aws ec2 describe-instances \
  --filters "Name=tag:ManagedBy,Values=Slurm" \
            "Name=instance-state-name,Values=pending,running" \
  --query "Reservations[].Instances[].[InstanceId,State.Name,PrivateIpAddress,Tags[?Key==\`Name\`].Value|[0]]" \
  --output table'
```

#### 5C. Test Multi-Node Job

```bash
# Submit MPI job across 10 cloud nodes
srun -p cloud -N 10 hostname

# This will launch 10 instances in AWS
# All will mount your on-prem NFS
# All will authenticate with your Munge key
# All will join your cluster
```

## Troubleshooting

### Problem: Nodes Stuck in `alloc#` State

**Cause**: Instances failing to launch or start slurmd

**Solutions:**

1. **Check plugin logs:**
   ```bash
   tail -100 /var/log/slurm/aws_plugin.log
   ```

2. **Check if instances launched:**
   ```bash
   aws ec2 describe-instances \
     --filters "Name=tag:Name,Values=cloud-burst-*" \
     --query "Reservations[].Instances[].[InstanceId,State.Name,StateReason.Message]"
   ```

3. **Check instance system log (if instance exists):**
   ```bash
   aws ec2 get-console-output --instance-id i-xxxxx
   ```

Common issues:
- NFS mount failing (check network connectivity)
- Munge key mismatch (verify key is identical)
- Slurm version mismatch (check AMI has exact version)
- Network connectivity (VPN down?)

### Problem: Nodes Not Terminating

**Cause**: `suspend.py` cannot find instances

**Solutions:**

1. **Verify Name tags:**
   ```bash
   aws ec2 describe-instances \
     --filters "Name=instance-state-name,Values=running" \
     --query "Reservations[].Instances[].[InstanceId,Tags[?Key=='Name'].Value|[0]]"
   ```

2. **Check suspend.py logs:**
   ```bash
   grep "suspend" /var/log/slurm/aws_plugin.log
   ```

3. **Manual termination (if needed):**
   ```bash
   aws ec2 terminate-instances --instance-ids i-xxxxx
   scontrol update NodeName=cloud-burst-0 State=DOWN Reason="manual termination"
   scontrol update NodeName=cloud-burst-0 State=POWER_DOWN
   ```

### Problem: NFS Mount Fails on Cloud Nodes

**Cause**: Network connectivity or NFS export issues

**Solutions:**

1. **Test NFS from AWS test instance:**
   ```bash
   # Launch test instance in same subnet
   # SSH in and test:
   showmount -e 10.0.1.100
   sudo mount -t nfs 10.0.1.100:/nfs /mnt
   ls /mnt/slurm
   ```

2. **Check NFS exports on headnode:**
   ```bash
   exportfs -v
   # Should include your AWS subnet CIDR
   ```

3. **Add AWS subnet to exports:**
   ```bash
   echo "/nfs 10.1.0.0/16(rw,sync,no_root_squash)" >> /etc/exports
   exportfs -ra
   ```

4. **Check firewall allows NFS (port 2049):**
   ```bash
   sudo firewall-cmd --list-all
   sudo firewall-cmd --add-service=nfs --permanent
   sudo firewall-cmd --reload
   ```

### Problem: Munge Authentication Fails

**Cause**: Time skew or mismatched keys

**Solutions:**

1. **Ensure time sync:**
   ```bash
   # On cloud nodes (in AMI):
   sudo systemctl enable chronyd
   sudo systemctl start chronyd
   chronyc tracking
   ```

2. **Verify Munge key identical:**
   ```bash
   # On headnode:
   md5sum /etc/munge/munge.key

   # On cloud node:
   md5sum /etc/munge/munge.key
   # Must match exactly
   ```

3. **Test Munge between nodes:**
   ```bash
   # On headnode:
   munge -n | ssh cloud-node-ip unmunge
   ```

## Cost Optimization

### Spot Instances

For fault-tolerant workloads, use Spot instances (up to 90% savings):

```json
{
  "PurchasingOption": "spot",
  "SpotOptions": {
    "AllocationStrategy": "price-capacity-optimized",
    "MaxPrice": "0.50"
  }
}
```

### Right-Sizing

Monitor actual CPU/memory usage and adjust instance types:

```bash
# Check Slurm accounting
sacct -X -o JobID,NodeList,ReqCPUS,ReqMem,MaxRSS,CPUTime
```

### Auto-Scaling

Adjust `MaxNodes` based on workload patterns. Start conservative:

```json
"MaxNodes": 20  // Start here, increase as needed
```

## Next Steps

- Review [Performance Tuning Guide](performance-tuning.md) for optimization
- Set up [Monitoring](monitoring.md) with CloudWatch
- See [Advanced Usage](advanced-usage.md) for GPU bursting, multi-region, etc.

## Summary

You've now configured cloud bursting from your on-premises Slurm cluster to AWS! Your cluster can:
- Use local compute nodes for regular workloads
- Automatically burst to AWS when local capacity is full
- Terminate AWS instances when no longer needed
- Pay only for cloud compute when actually used

**Key files created:**
- `/etc/slurm/aws-plugin/config.json` - Plugin configuration
- `/etc/slurm/aws-plugin/partitions.json` - Cloud node groups
- AMI: `$SLURM_AMI` - Your custom Slurm compute image
- Launch Template: `$TEMPLATE_ID` - Instance launch configuration
