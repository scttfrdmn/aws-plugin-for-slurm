# On-Premises to AWS Cloud Bursting Guide

This guide walks you through setting up cloud bursting from your **on-premises Slurm cluster** to AWS. This is the primary use case for this plugin.

## What is Cloud Bursting?

Cloud bursting allows your on-premises HPC cluster to dynamically provision additional compute capacity in AWS when your local resources are fully utilized. This gives you:

- **Overflow capacity** - Handle workload spikes without buying more hardware
- **Access to specialized hardware** - GPU instances, high-memory instances, etc.
- **Cost efficiency** - Pay only for what you use, when you use it

## Architecture Overview

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
- AWS compute nodes mount shared storage from on-prem
- Plugin runs on on-prem headnode, launches instances in AWS
- No DNS required - plugin injects IPs directly into Slurm
- Authentication (Munge) works across the VPN

---

## What Needs to Happen: The Roadmap

Before diving into the step-by-step instructions, here's a high-level overview of what you'll be setting up:

### 1. Network Connectivity (Critical Foundation)
**What**: Establish private network connection between your on-prem data center and AWS VPC

**Options**: VPN (easier, ~30 min setup) or Direct Connect (production-grade, days to setup)

**Why it matters**: Cloud compute nodes need to communicate with your on-prem headnode for Slurm control messages, Munge authentication, and shared storage access

**Common variations**:
- Site-to-Site VPN (most common for testing/light workloads)
- AWS Direct Connect (production workloads with heavy data transfer)
- Transit Gateway (multi-VPC environments)

---

### 2. User Identity Consistency (UID/GID Synchronization)
**What**: Ensure user IDs (UIDs) and group IDs (GIDs) match between on-prem and cloud nodes

**Why it matters**: File ownership and permissions must be consistent across all nodes. If user `jdoe` is UID 5001 on-prem but UID 6002 in cloud, file access breaks.

**Common variations**:
- **LDAP/Active Directory** - Centralized directory (most common in enterprise)
- **NIS/NIS+** - Older but still used in HPC environments
- **SSSD** - Modern system security services (works with LDAP/AD)
- **Manual passwd/group sync** - Simple but fragile (copy files around)
- **Cloud-based directory** - AWS Managed Microsoft AD, IdP federation

**What we'll show**: Illustrative example using SSSD + LDAP (most common). If you use something else, the key requirement is: **same UID/GID on all nodes**.

---

### 3. Shared Filesystem Access
**What**: Cloud nodes need access to shared directories (/home, Slurm binaries, job data)

**Why it matters**: Jobs expect to read input files and write output to shared storage. Slurm binaries must be accessible at the same path on all nodes.

**Common variations**:
- **NFS from on-prem** - Mount on-prem NFS server over VPN (simple, works for light I/O)
- **AWS EFS** - Cloud-native NFS, but data must be replicated from on-prem
- **FSx for Lustre** - High-performance parallel filesystem (for HPC workloads)
- **FSx for NetApp ONTAP** - Enterprise NAS in cloud
- **BeeGFS/GPFS** - Parallel filesystems (advanced)

**What we'll show**: Illustrative example using NFS from on-prem (simplest). If you use Lustre/GPFS/etc., adapt the mount configuration.

---

### 4. Build Matching AMI (Most Critical Technical Step)
**What**: Create an AWS AMI with Slurm installed at the **exact same version** as your on-prem cluster

**Why it matters**: Slurm requires binary compatibility between slurmctld (headnode) and slurmd (compute nodes). Version mismatch = nodes won't join cluster.

**This is NOT flexible**: If on-prem is 20.02.3, cloud must be 20.02.3. Not 20.02.4, not 21.08.0, exactly 20.02.3.

**What we'll show**: Concrete steps using Packer (automated) or manual build. This section is prescriptive - follow it exactly.

---

### 5. Configure AWS Infrastructure
**What**: Set up IAM roles, security groups, launch templates for cloud compute instances

**Why it matters**: Defines how instances launch, what permissions they have, network access rules

**What we'll show**: Concrete AWS CLI commands. Mostly prescriptive, with customization points for instance types, regions, etc.

---

### 6. Install Plugin on Headnode
**What**: Deploy Python scripts on your on-prem headnode that Slurm calls to launch/terminate cloud instances

**Why it matters**: This is the bridge between Slurm and AWS - translates Slurm power save events into EC2 API calls

**What we'll show**: Concrete installation and configuration steps. Prescriptive.

---

### 7. Configure Slurm for Cloud Bursting
**What**: Update slurm.conf to enable power save mode and define cloud partition

**Why it matters**: Tells Slurm how to manage cloud nodes (when to power up/down, timeouts, etc.)

**What we'll show**: Configuration file generation and integration with your existing Slurm setup. Some customization based on your policies.

---

### 8. Test and Validate
**What**: Submit test jobs, monitor instance lifecycle, verify everything works

**Why it matters**: Catch configuration issues before production workloads

**What we'll show**: Concrete test procedures and troubleshooting steps.

---

## Prerequisites

Before starting, verify you have:

### On-Premises Side
- ✅ **Working Slurm cluster** (version 20.02.3 or later recommended)
  - Headnode with slurmctld running
  - Power save mode support (check: `scontrol show config | grep SuspendProgram`)
  - Root or slurm user access
- ✅ **Shared storage** accessible from headnode (NFS, Lustre, GPFS, etc.)
- ✅ **Munge authentication** configured and working
- ✅ **User management system** (LDAP, NIS, or manual - doesn't matter which, as long as it's consistent)
- ✅ **Public IP address** for VPN endpoint (or Direct Connect setup)
- ✅ **Network admin access** to configure VPN/routing

**Document these now:**
```bash
# On your headnode, document:
slurmctld --version
# Example output: slurm 20.02.3
# Write this down: _________________

which slurmctld
# Example output: /usr/local/bin/slurmctld or /nfs/slurm/bin/slurmctld
# Slurm prefix: _________________

showmount -e localhost
# Example output: /home, /nfs, /scratch
# NFS exports: _________________

# Your on-prem network CIDR (e.g., 10.0.1.0/24):
# Network CIDR: _________________

# Your public IP for VPN endpoint:
# Public IP: _________________
```

### AWS Side
- ✅ **AWS account** with EC2 access
- ✅ **IAM permissions** to create:
  - VPCs, subnets, VPN gateways
  - EC2 instances, launch templates
  - IAM roles and policies
  - Secrets Manager secrets (optional, for Munge key)
- ✅ **AWS CLI installed** and configured (`aws configure`)
- ✅ **Budget/cost approval** for compute instances

---

## Step-by-Step Setup

### Step 1: Establish Network Connectivity

**Goal**: Create private network connection between on-prem and AWS so compute nodes can reach your headnode.

**Time estimate**: 30-60 minutes for VPN, days/weeks for Direct Connect

#### 1.1: Create AWS VPC and Subnets

**[Concrete - do this exactly]**

```bash
# Set your region
export AWS_REGION=us-east-1

# Create VPC (10.1.0.0/16 - adjust if conflicts with on-prem)
# ⚠️  Check your on-prem CIDR first! If you use 10.1.x.x on-prem, pick different CIDR
VPC_ID=$(aws ec2 create-vpc \
  --cidr-block 10.1.0.0/16 \
  --region $AWS_REGION \
  --tag-specifications 'ResourceType=vpc,Tags=[{Key=Name,Value=slurm-burst-vpc}]' \
  --query 'Vpc.VpcId' \
  --output text)

echo "VPC ID: $VPC_ID"
# Save this: export VPC_ID=vpc-xxxxx

# Create subnet for compute nodes (10.1.1.0/24)
SUBNET_ID=$(aws ec2 create-subnet \
  --vpc-id $VPC_ID \
  --cidr-block 10.1.1.0/24 \
  --availability-zone ${AWS_REGION}a \
  --tag-specifications 'ResourceType=subnet,Tags=[{Key=Name,Value=slurm-compute-subnet}]' \
  --query 'Subnet.SubnetId' \
  --output text)

echo "Subnet ID: $SUBNET_ID"
# Save this: export SUBNET_ID=subnet-xxxxx

# Enable DNS hostnames and support
aws ec2 modify-vpc-attribute \
  --vpc-id $VPC_ID \
  --enable-dns-hostnames

aws ec2 modify-vpc-attribute \
  --vpc-id $VPC_ID \
  --enable-dns-support
```

---

#### 1.2: Set Up Network Connectivity

**[Illustrative - adapt to your environment]**

You have two main options:

##### Option A: Site-to-Site VPN (Recommended for Getting Started)

**Pros**: Fast setup (~30 min), low cost (~$36/month), good for testing and light workloads
**Cons**: Internet-based (variable latency), ~1.25 Gbps per tunnel limit

**AWS side setup:**

```bash
# 1. Create Customer Gateway (your on-prem router's public IP)
ONPREM_PUBLIC_IP="203.0.113.50"  # ⚠️  Replace with YOUR public IP

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

# Wait for attachment
sleep 10

# 4. Create VPN Connection
VPN_ID=$(aws ec2 create-vpn-connection \
  --type ipsec.1 \
  --customer-gateway-id $CGW_ID \
  --vpn-gateway-id $VGW_ID \
  --options "StaticRoutesOnly=false,TunnelOptions=[{PreSharedKey=YourStrongPSK123456789}]" \
  --tag-specifications 'ResourceType=vpn-connection,Tags=[{Key=Name,Value=slurm-vpn}]' \
  --query 'VpnConnection.VpnConnectionId' \
  --output text)

echo "VPN Connection ID: $VPN_ID"

# 5. Download VPN configuration for your on-prem router
aws ec2 describe-vpn-connections \
  --vpn-connection-ids $VPN_ID \
  --query 'VpnConnections[0].CustomerGatewayConfiguration' \
  --output text > vpn-config.xml

echo "VPN configuration saved to vpn-config.xml"
echo "Use this to configure your on-prem VPN endpoint"
```

**On-premises side configuration:**

The specific steps depend on your router/firewall. Here are examples for common platforms:

<details>
<summary><b>📘 Linux with strongSwan/Libreswan</b></summary>

```bash
# Install strongSwan (RHEL/CentOS/Rocky)
sudo yum install -y strongswan

# Or Libreswan (alternative)
# sudo yum install -y libreswan

# Extract tunnel IPs from vpn-config.xml
# You'll need: AWS tunnel endpoint IPs, your pre-shared key, inside tunnel CIDRs

# Example /etc/strongswan/ipsec.conf
sudo tee /etc/strongswan/ipsec.conf > /dev/null <<'EOF'
config setup
    charondebug="ike 2, knl 2, cfg 2"

conn aws-tunnel1
    auto=start
    left=%defaultroute
    leftid=203.0.113.50  # Your public IP
    leftsubnet=10.0.1.0/24  # Your on-prem network
    right=52.1.2.3  # AWS tunnel 1 endpoint (from vpn-config.xml)
    rightsubnet=10.1.0.0/16  # AWS VPC CIDR
    type=tunnel
    ikelifetime=8h
    keylife=1h
    rekeymargin=3m
    keyingtries=3
    authby=secret
    keyexchange=ikev1
    ike=aes128-sha1-modp1024
    esp=aes128-sha1-modp1024
EOF

# Configure pre-shared key
sudo tee /etc/strongswan/ipsec.secrets > /dev/null <<'EOF'
203.0.113.50 52.1.2.3 : PSK "YourStrongPSK123456789"
EOF

sudo chmod 600 /etc/strongswan/ipsec.secrets

# Start VPN
sudo systemctl enable strongswan
sudo systemctl start strongswan

# Check status
sudo strongswan status
```
</details>

<details>
<summary><b>📘 pfSense / OPNsense</b></summary>

1. VPN > IPsec > Tunnels > Add P1
2. Key Exchange version: IKEv1
3. Remote Gateway: `52.1.2.3` (from vpn-config.xml)
4. Pre-Shared Key: `YourStrongPSK123456789`
5. Encryption: AES 128, Hash: SHA1, DH Group: 2
6. Add P2 (Phase 2):
   - Local Network: Your on-prem CIDR (10.0.1.0/24)
   - Remote Network: AWS VPC CIDR (10.1.0.0/16)
   - Protocol: ESP, Encryption: AES 128, Hash: SHA1
7. Apply changes
8. Check Status > IPsec for tunnel status
</details>

<details>
<summary><b>📘 Cisco IOS/ASA</b></summary>

See AWS documentation for Cisco-specific configuration:
```bash
# AWS provides downloadable config for many vendors
aws ec2 describe-vpn-connections \
  --vpn-connection-ids $VPN_ID \
  --query 'VpnConnections[0].CustomerGatewayConfiguration' \
  --output text | grep -A 50 "Cisco"
```
</details>

<details>
<summary><b>📘 Fortinet FortiGate</b></summary>

1. VPN > IPsec Wizard
2. Template: Custom
3. Remote Gateway: `52.1.2.3`
4. Pre-shared Key: `YourStrongPSK123456789`
5. Phase 1: IKEv1, AES-128, SHA-1, DH Group 2
6. Phase 2: AES-128, SHA-1, No PFS
7. Local Address: Your on-prem subnet (10.0.1.0/24)
8. Remote Address: AWS VPC CIDR (10.1.0.0/16)
9. Create static route to 10.1.0.0/16 via tunnel interface
</details>

</details>

##### Option B: AWS Direct Connect (Production Workloads)

**Pros**: Dedicated fiber (1-100 Gbps), predictable latency, better for heavy data transfer
**Cons**: Longer setup time (days/weeks), higher cost, requires colocation or carrier

**When to use**: Production deployments with heavy data transfer or latency requirements

**Setup**: Contact AWS support or your network team. Configuration after connection is established is similar to VPN (routing setup).

> **📝 Note**: Once Direct Connect is established, the rest of this guide is identical - you'll just have better bandwidth/latency.

---

#### 1.3: Configure Routing

**[Concrete - do this]**

**AWS side:**

```bash
# Get your VPC's main route table
ROUTE_TABLE_ID=$(aws ec2 describe-route-tables \
  --filters "Name=vpc-id,Values=$VPC_ID" "Name=association.main,Values=true" \
  --query 'RouteTables[0].RouteTableId' \
  --output text)

echo "Route Table ID: $ROUTE_TABLE_ID"

# Enable route propagation from VGW (for VPN)
aws ec2 enable-vgw-route-propagation \
  --route-table-id $ROUTE_TABLE_ID \
  --gateway-id $VGW_ID

# Verify routes are propagating
aws ec2 describe-route-tables \
  --route-table-ids $ROUTE_TABLE_ID \
  --query 'RouteTables[0].Routes' \
  --output table

# You should see routes to your on-prem CIDR
```

**On-premises side:**

Add static route to AWS VPC CIDR. The exact method depends on your setup:

```bash
# Option 1: Temporary test (Linux)
sudo ip route add 10.1.0.0/16 dev ipsec0
# Or via tunnel gateway: sudo ip route add 10.1.0.0/16 via 169.254.0.1

# Option 2: Persistent (add to /etc/sysconfig/network-scripts/route-* or network manager)

# Option 3: On your router (pfSense, FortiGate, etc.)
# Add static route: Destination 10.1.0.0/16 -> Gateway (VPN tunnel interface)
```

> **📝 Variation**: If using BGP with Direct Connect or advanced VPN, routes propagate automatically. Static routes shown here for simplicity.

---

#### 1.4: Test Connectivity

**[Concrete - validation steps]**

```bash
# 1. Check VPN tunnel status (if using VPN)
aws ec2 describe-vpn-connections \
  --vpn-connection-ids $VPN_ID \
  --query 'VpnConnections[0].VgwTelemetry' \
  --output table

# Look for Status: UP on at least one tunnel

# 2. Launch temporary test instance in AWS
TEST_AMI=$(aws ec2 describe-images \
  --owners amazon \
  --filters "Name=name,Values=amzn2-ami-hvm-*-x86_64-gp2" \
            "Name=state,Values=available" \
  --query 'Images | sort_by(@, &CreationDate) | [-1].ImageId' \
  --output text)

TEST_SG=$(aws ec2 create-security-group \
  --group-name temp-test-sg \
  --description "Temp security group for connectivity testing" \
  --vpc-id $VPC_ID \
  --query 'GroupId' \
  --output text)

# Allow ICMP from on-prem
ONPREM_CIDR="10.0.1.0/24"  # ⚠️  Your on-prem network CIDR
aws ec2 authorize-security-group-ingress \
  --group-id $TEST_SG \
  --protocol icmp \
  --port -1 \
  --cidr $ONPREM_CIDR

TEST_INSTANCE=$(aws ec2 run-instances \
  --image-id $TEST_AMI \
  --instance-type t3.micro \
  --subnet-id $SUBNET_ID \
  --security-group-ids $TEST_SG \
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
echo ""
echo "From your on-prem headnode, try:"
echo "  ping -c 4 $TEST_IP"
```

**From your on-prem headnode:**

```bash
# Test basic connectivity
ping -c 4 10.1.1.X  # Use the TEST_IP from above

# If ping works, connectivity is established! ✅

# Clean up test instance (back on machine with AWS CLI)
aws ec2 terminate-instances --instance-ids $TEST_INSTANCE
aws ec2 delete-security-group --group-id $TEST_SG
```

**If ping fails:**
- Check VPN tunnel status (should be UP)
- Verify security group allows ICMP
- Check on-prem firewall rules
- Verify routing tables on both sides
- Use connectivity validation script: `scripts/validate-onprem-connectivity.sh`

---

### Step 2: Ensure User Identity Consistency

**Goal**: Make sure user IDs (UIDs) and group IDs (GIDs) are consistent across on-prem and cloud nodes.

**Time estimate**: 15-30 minutes if you already have centralized identity, 1-2 hours if setting up new

**Why this matters**: When a user submits a job, Slurm runs it as that user. Files on shared storage must have correct ownership. If `jdoe` is UID 5001 on-prem but UID 6002 on cloud nodes, permissions break.

#### 2.1: Identify Your Current Setup

**[Illustrative - document what YOU use]**

What user management system does your on-prem cluster use?

```bash
# On your on-prem headnode, check:

# LDAP/Active Directory?
getent passwd | head -5
# If you see: jdoe:x:5001:5001:John Doe:/home/jdoe:/bin/bash
# And users aren't in /etc/passwd, likely using LDAP/AD

# Check for SSSD
systemctl status sssd
# If running, you're using SSSD (often with LDAP/AD backend)

# NIS/NIS+?
ypwhich
# If it returns a server, you're using NIS

# Manual /etc/passwd?
grep jdoe /etc/passwd
# If users are directly in this file, using local files

# Active Directory?
realm list
# If domain is configured, using AD

# Document your answer: _______________
```

<details>
<summary><b>📘 Option 1: LDAP/Active Directory with SSSD (Most Common)</b></summary>

**If your on-prem cluster uses LDAP/AD**, you have two approaches for cloud nodes:

**Approach A: Cloud nodes join same LDAP/AD (Recommended)**

```bash
# On your AMI build instance (Step 4), install SSSD
sudo yum install -y sssd sssd-ldap oddjob-mkhomedir

# Copy SSSD config from on-prem headnode
scp headnode:/etc/sssd/sssd.conf /tmp/sssd.conf
sudo mv /tmp/sssd.conf /etc/sssd/sssd.conf
sudo chmod 600 /etc/sssd/sssd.conf

# Enable services
sudo systemctl enable sssd oddjobd
sudo systemctl start sssd oddjobd

# Test
id jdoe
# Should show: uid=5001(jdoe) gid=5001(users)
# Must match on-prem exactly
```

**Approach B: Replicate UIDs via local files (Simple but fragile)**

```bash
# On on-prem headnode, export users
getent passwd > /tmp/passwd.export
getent group > /tmp/group.export

# Copy to AMI build instance
scp /tmp/passwd.export ami-builder:/tmp/
scp /tmp/group.export ami-builder:/tmp/

# On AMI builder, merge (carefully!)
sudo bash -c 'cat /tmp/passwd.export >> /etc/passwd'
sudo bash -c 'cat /tmp/group.export >> /etc/group'
```

> ⚠️  Approach B is fragile - new users won't sync automatically. Use Approach A for production.

</details>

<details>
<summary><b>📘 Option 2: NIS/NIS+</b></summary>

If using NIS:

```bash
# On AMI build instance
sudo yum install -y ypbind

# Configure NIS domain and server
sudo nisdomainname YOUR_NIS_DOMAIN
echo "NISDOMAIN=YOUR_NIS_DOMAIN" | sudo tee -a /etc/sysconfig/network

sudo tee /etc/yp.conf > /dev/null <<EOF
ypserver 10.0.1.100  # Your NIS server IP (on-prem)
EOF

# Start NIS client
sudo systemctl enable ypbind
sudo systemctl start ypbind

# Test
ypwhich
id jdoe
```

> **📝 Note**: Ensure NIS server (on-prem) is reachable from AWS over VPN

</details>

<details>
<summary><b>📘 Option 3: Local /etc/passwd files (Simple Clusters)</b></summary>

If you just copy /etc/passwd around:

```bash
# On on-prem headnode
sudo scp /etc/passwd /etc/group /etc/shadow ami-builder:/tmp/

# On AMI builder
# ⚠️  Careful - this overwrites system users! Better to merge manually
sudo cp /tmp/passwd /etc/passwd
sudo cp /tmp/group /etc/group
sudo cp /tmp/shadow /etc/shadow
sudo chmod 640 /etc/shadow
```

> ⚠️  Not recommended for production - fragile and doesn't scale

</details>

---

#### 2.2: Validate Consistency

**[Concrete - do this validation]**

```bash
# Pick a test user that exists on on-prem
TEST_USER="jdoe"  # Replace with actual username

# On on-prem headnode:
id $TEST_USER
# Example output: uid=5001(jdoe) gid=5001(users) groups=5001(users),500(hpc)

# On AMI build instance (later in Step 4):
id $TEST_USER
# Output MUST match exactly:
# uid=5001(jdoe) gid=5001(users) groups=5001(users),500(hpc)
#     ^^^^^                ^^^^^         ^^^^^     ^^^
#     These must all be identical
```

**Critical**: UID and primary GID must match. Secondary groups should match but are less critical.

> **📝 What matters**: Consistent UID/GID across all nodes. HOW you achieve it (LDAP, NIS, files) doesn't matter to Slurm.

---

### Step 3: Configure Shared Filesystem Access

**Goal**: Cloud nodes need access to shared directories (/home, Slurm binaries, job scratch space)

**Time estimate**: 30-60 minutes

**Why this matters**: Jobs expect to read/write files on shared storage. Slurm binaries (slurmd, srun, etc.) must be at the same path on all nodes.

#### 3.1: Identify Your Current Setup

**[Illustrative - document what YOU use]**

What shared filesystem does your cluster use?

```bash
# On on-prem headnode:
df -h | grep -E "nfs|lustre|gpfs|beegfs"

# Common patterns:
# NFS:      10.0.1.100:/nfs on /nfs type nfs4
# Lustre:   10.0.1.101@tcp:/lustrefs on /lustre type lustre
# GPFS:     /dev/gpfs on /gpfs type gpfs
# BeeGFS:   beegfs_nodev on /beegfs type beegfs

# What directories are shared?
mount | grep -E "nfs|lustre|gpfs|beegfs"

# Common shared directories:
# /home          - User home directories (REQUIRED)
# /nfs or /shared - Slurm installation, job data
# /scratch       - Fast scratch space for jobs

# Document:
# Filesystem type: _______________
# Shared mounts: _______________
```

---

#### 3.2: Configure Cloud Node Access

**[Illustrative - adapt to YOUR storage]**

<details>
<summary><b>📘 Option 1: NFS from On-Prem (Simplest)</b></summary>

**Good for**: Light I/O workloads, testing, small bursts

**Limitations**: Network latency over VPN, limited bandwidth

**On on-prem headnode (NFS server):**

```bash
# Check current NFS exports
sudo exportfs -v

# Add AWS VPC CIDR to exports (if not already present)
AWS_VPC_CIDR="10.1.0.0/16"

# Edit /etc/exports
sudo tee -a /etc/exports > /dev/null <<EOF
/nfs ${AWS_VPC_CIDR}(rw,sync,no_root_squash,no_subtree_check)
/home ${AWS_VPC_CIDR}(rw,sync,no_root_squash,no_subtree_check)
EOF

# Re-export
sudo exportfs -ra

# Verify
sudo exportfs -v | grep 10.1

# Allow NFS through firewall (if using firewalld)
sudo firewall-cmd --permanent --add-service=nfs
sudo firewall-cmd --permanent --add-service=mountd
sudo firewall-cmd --permanent --add-service=rpc-bind
sudo firewall-cmd --reload
```

**On cloud nodes (AMI build instance in Step 4):**

```bash
# Test NFS mount
HEADNODE_IP="10.0.1.100"  # Your on-prem headnode IP
NFS_EXPORT="/nfs"          # Your NFS export path

sudo mkdir -p /nfs /home
sudo mount -t nfs ${HEADNODE_IP}:${NFS_EXPORT} /nfs
sudo mount -t nfs ${HEADNODE_IP}:/home /home

# Verify
ls -la /nfs
ls -la /home

# If successful, add to /etc/fstab for automatic mounting
sudo tee -a /etc/fstab > /dev/null <<EOF
${HEADNODE_IP}:/nfs   /nfs   nfs   defaults,_netdev,rsize=1048576,wsize=1048576,hard,timeo=600,retrans=2   0 0
${HEADNODE_IP}:/home  /home  nfs   defaults,_netdev,rsize=1048576,wsize=1048576,hard,timeo=600,retrans=2   0 0
EOF

# Test /etc/fstab
sudo umount /nfs /home
sudo mount -a
df -h | grep nfs
```

**NFS tuning for WAN (VPN) access:**

```bash
# Add these options to /etc/fstab entries:
# rsize=1048576,wsize=1048576  - Larger read/write buffers
# hard                          - Don't give up on timeouts
# timeo=600                     - 60 second timeout (10x default)
# retrans=2                     - Retry twice
# _netdev                       - Wait for network before mounting
```

</details>

<details>
<summary><b>📘 Option 2: AWS EFS (Cloud-Native NFS)</b></summary>

**Good for**: When you want cloud-native storage, don't have heavy NFS already

**Limitations**: Data must be synced from on-prem, costs, not as fast as on-prem parallel FS

**Create EFS:**

```bash
# Create EFS filesystem
EFS_ID=$(aws efs create-file-system \
  --performance-mode generalPurpose \
  --throughput-mode bursting \
  --encrypted \
  --tags Key=Name,Value=slurm-shared-storage \
  --query 'FileSystemId' \
  --output text)

# Create mount targets in your subnet
aws efs create-mount-target \
  --file-system-id $EFS_ID \
  --subnet-id $SUBNET_ID \
  --security-groups $SG_ID

# Get EFS DNS name
EFS_DNS="${EFS_ID}.efs.${AWS_REGION}.amazonaws.com"
echo "EFS Mount: $EFS_DNS"
```

**Mount on cloud nodes:**

```bash
# In AMI build instance
sudo yum install -y amazon-efs-utils

# Test mount
sudo mkdir -p /nfs
sudo mount -t efs ${EFS_ID}:/ /nfs

# Add to /etc/fstab
echo "${EFS_ID}:/ /nfs efs defaults,_netdev 0 0" | sudo tee -a /etc/fstab
```

**Sync data from on-prem:**

```bash
# On on-prem headnode, sync data to EFS
# Option 1: DataSync (AWS service - recommended)
# Option 2: rsync over VPN
rsync -avz --progress /nfs/ /mnt/efs/
```

> **📝 Note**: EFS adds cost and complexity. Only use if you need cloud-native storage or on-prem NFS is inadequate.

</details>

<details>
<summary><b>📘 Option 3: FSx for Lustre (High-Performance HPC)</b></summary>

**Good for**: HPC workloads with heavy parallel I/O

**Limitations**: Cost, complexity, requires data sync/replication

```bash
# Create FSx for Lustre
FSX_ID=$(aws fsx create-file-system \
  --file-system-type LUSTRE \
  --lustre-configuration "DeploymentType=PERSISTENT_1,PerUnitStorageThroughput=200" \
  --storage-capacity 1200 \
  --subnet-ids $SUBNET_ID \
  --security-group-ids $SG_ID \
  --query 'FileSystem.FileSystemId' \
  --output text)

# Wait for creation (takes ~10 minutes)
aws fsx describe-file-systems --file-system-ids $FSX_ID

# Get mount name
FSX_MOUNT=$(aws fsx describe-file-systems \
  --file-system-ids $FSX_ID \
  --query 'FileSystems[0].LustreConfiguration.MountName' \
  --output text)

FSX_DNS=$(aws fsx describe-file-systems \
  --file-system-ids $FSX_ID \
  --query 'FileSystems[0].DNSName' \
  --output text)

echo "Mount command: sudo mount -t lustre ${FSX_DNS}@tcp:/${FSX_MOUNT} /lustre"
```

**Mount on cloud nodes:**

```bash
# Install Lustre client (must match FSx version)
sudo amazon-linux-extras install -y lustre2.12

sudo mkdir -p /lustre
sudo mount -t lustre ${FSX_DNS}@tcp:/${FSX_MOUNT} /lustre

# Add to /etc/fstab
echo "${FSX_DNS}@tcp:/${FSX_MOUNT} /lustre lustre defaults,_netdev 0 0" | sudo tee -a /etc/fstab
```

> **📝 Note**: FSx for Lustre is expensive and complex. Only for workloads that need parallel filesystem performance.

</details>

<details>
<summary><b>📘 Option 4: Existing Lustre/GPFS/BeeGFS from On-Prem</b></summary>

If you already have a parallel filesystem on-prem:

**Lustre:**
```bash
# On cloud nodes, install matching Lustre client version
# Check on-prem version: lctl get_param version
sudo yum install -y lustre-client-X.Y.Z  # Match on-prem version

# Mount
sudo mkdir -p /lustre
sudo mount -t lustre mgs_ip@tcp:/fsname /lustre
```

**GPFS:**
```bash
# Install GPFS client on cloud nodes
# Copy /etc/mmsf* config from on-prem
# Join cluster: mmaddnode, mmstartup, etc.
```

**BeeGFS:**
```bash
# Install BeeGFS client
# Copy /etc/beegfs/beegfs-client.conf from on-prem
# Mount: sudo beegfs-ctl --mount=/beegfs
```

> **📝 Note**: Parallel filesystems over WAN (VPN) can be tricky - consider latency and bandwidth.

</details>

---

#### 3.3: Validate Shared Filesystem Access

**[Concrete - do this validation]**

```bash
# On your on-prem headnode, create a test file
echo "Testing from on-prem" | sudo tee /nfs/slurm-burst-test.txt
ls -la /nfs/slurm-burst-test.txt

# On cloud test instance (or AMI builder in Step 4):
cat /nfs/slurm-burst-test.txt
# Should output: Testing from on-prem

# Test write access
echo "Testing from AWS" | sudo tee /nfs/aws-test.txt

# Back on headnode, verify:
cat /nfs/aws-test.txt
# Should output: Testing from AWS

# Cleanup
sudo rm /nfs/slurm-burst-test.txt /nfs/aws-test.txt
```

**If file access fails:**
- Check NFS exports on headnode (exportfs -v)
- Verify network connectivity (can you ping headnode from cloud?)
- Check firewall rules (port 2049 for NFS)
- Verify security group allows on-prem CIDR
- Check mount options in /etc/fstab

> **📝 What matters**: Cloud nodes can read AND write to shared storage, files maintain ownership (check with ls -la).

---

### Step 4: Build AWS AMI with Matching Slurm Version

**Goal**: Create AMI with Slurm installed at the EXACT same version as on-prem

**Time estimate**: 1-2 hours (manual) or 20 minutes (Packer automation)

**Why this matters**: This is the **most critical step**. Slurm requires binary compatibility. Wrong version = nodes won't join cluster.

#### 4.1: Choose Your Approach

You have two options:

**Option A: Automated with Packer (Recommended)**
- Consistent, reproducible builds
- Rebuild AMI in ~15 minutes when Slurm updates
- Documented in [examples/packer/](../examples/packer/)

**Option B: Manual AMI Building (Detailed below)**
- Step-by-step control
- Good for understanding the process
- More time-consuming

We'll show Option B (manual) in detail. For Option A, see [Packer README](../examples/packer/README.md).

---

#### 4.2: Launch Base Instance for AMI Building

**[Concrete - do this]**

```bash
# Find latest Amazon Linux 2 AMI
BASE_AMI=$(aws ec2 describe-images \
  --owners amazon \
  --filters "Name=name,Values=amzn2-ami-hvm-*-x86_64-gp2" \
            "Name=state,Values=available" \
  --query 'Images | sort_by(@, &CreationDate) | [-1].ImageId' \
  --output text)

echo "Base AMI: $BASE_AMI"

# Create key pair if you don't have one
# aws ec2 create-key-pair --key-name slurm-builder --query 'KeyMaterial' --output text > slurm-builder.pem
# chmod 400 slurm-builder.pem

# Create security group for building (temporary)
BUILD_SG=$(aws ec2 create-security-group \
  --group-name slurm-ami-builder \
  --description "Temp SG for building Slurm AMI" \
  --vpc-id $VPC_ID \
  --query 'GroupId' \
  --output text)

# Allow SSH from your IP (for manual building)
YOUR_IP=$(curl -s ifconfig.me)
aws ec2 authorize-security-group-ingress \
  --group-id $BUILD_SG \
  --protocol tcp \
  --port 22 \
  --cidr ${YOUR_IP}/32

# Allow all from on-prem (for NFS testing)
aws ec2 authorize-security-group-ingress \
  --group-id $BUILD_SG \
  --protocol all \
  --cidr $ONPREM_CIDR

# Launch build instance
BUILD_INSTANCE=$(aws ec2 run-instances \
  --image-id $BASE_AMI \
  --instance-type t3.medium \
  --subnet-id $SUBNET_ID \
  --security-group-ids $BUILD_SG \
  --key-name slurm-builder \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=slurm-ami-builder}]' \
  --query 'Instances[0].InstanceId' \
  --output text)

echo "Build instance: $BUILD_INSTANCE"

# Wait for it to start
aws ec2 wait instance-running --instance-ids $BUILD_INSTANCE

# Get its private IP
BUILD_IP=$(aws ec2 describe-instances \
  --instance-ids $BUILD_INSTANCE \
  --query 'Reservations[0].Instances[0].PrivateIpAddress' \
  --output text)

echo ""
echo "Build instance ready!"
echo "Connect: ssh -i slurm-builder.pem ec2-user@$BUILD_IP"
echo "(If private IP, connect via headnode as jump host)"
```

---

#### 4.3: Install Slurm Dependencies

**[Concrete - SSH into build instance and run]**

```bash
# SSH into build instance
ssh -i slurm-builder.pem ec2-user@$BUILD_IP

# Update system
sudo yum update -y

# Install Slurm build dependencies
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
  libibmad libibumad \
  rpm-build perl gcc make \
  nfs-utils chrony \
  wget curl jq
```

---

#### 4.4: Build Slurm (EXACT Version Match)

**[Concrete - critical step]**

```bash
# Get your EXACT Slurm version from on-prem headnode
# Run this on headnode:
# slurmctld --version
# Output example: slurm 20.02.3

SLURM_VERSION="20.02.3"  # ⚠️  REPLACE with YOUR exact version

# Download exact version
cd /tmp
wget https://download.schedmd.com/slurm/slurm-${SLURM_VERSION}.tar.bz2

# If download fails, check available versions:
# curl -s https://download.schedmd.com/slurm/ | grep -o 'slurm-[0-9.]*.tar.bz2' | sort -V

tar xjf slurm-${SLURM_VERSION}.tar.bz2
cd slurm-${SLURM_VERSION}

# Configure Slurm with SAME PREFIX as on-prem
# Check on-prem: which slurmctld
# Example outputs:
#   /usr/local/bin/slurmctld  -> prefix is /usr/local
#   /nfs/slurm/bin/slurmctld  -> prefix is /nfs/slurm
#   /opt/slurm/bin/slurmctld  -> prefix is /opt/slurm

SLURM_PREFIX="/nfs/slurm"  # ⚠️  REPLACE with YOUR prefix path

./configure --prefix=$SLURM_PREFIX --sysconfdir=/etc/slurm

# Build (use all CPU cores)
make -j$(nproc)

# Install
sudo make install

# Verify version
$SLURM_PREFIX/sbin/slurmd --version
# Output MUST match on-prem exactly

# Clean up build files
cd /tmp
rm -rf slurm-*
```

---

#### 4.5: Create Slurm User and Directories

**[Concrete - run on build instance]**

```bash
# Create slurm user (match on-prem UID if possible)
# Check on-prem: id slurm

# If on-prem slurm is UID 1001:
sudo groupadd -g 1001 slurm || true
sudo useradd -u 1001 -g slurm -s /bin/bash -d /var/lib/slurm slurm || true

# Create required directories
sudo mkdir -p /var/spool/slurm /var/log/slurm
sudo chown -R slurm:slurm /var/spool/slurm /var/log/slurm
sudo chmod 755 /var/spool/slurm /var/log/slurm
```

---

#### 4.6: Configure User Identity (from Step 2)

**[Apply your choice from Step 2]**

If using LDAP/AD:
```bash
sudo yum install -y sssd sssd-ldap oddjob-mkhomedir
# Copy sssd.conf from on-prem or configure
sudo systemctl enable sssd oddjobd
```

If using NIS:
```bash
sudo yum install -y ypbind
# Configure NIS domain and server
sudo systemctl enable ypbind
```

**Validate:**
```bash
# Test that users resolve correctly
id jdoe  # Should match on-prem UID/GID
```

---

#### 4.7: Configure Shared Filesystem (from Step 3)

**[Apply your choice from Step 3]**

**For NFS from on-prem:**

```bash
HEADNODE_IP="10.0.1.100"  # Your headnode
NFS_EXPORT="/nfs"

# Test mount
sudo mkdir -p /nfs /home
sudo mount -t nfs ${HEADNODE_IP}:${NFS_EXPORT} /nfs
sudo mount -t nfs ${HEADNODE_IP}:/home /home

# Verify Slurm installation accessible
ls -la /nfs/slurm/sbin/slurmd
cat /home  # Should show home directories

# Add to /etc/fstab
sudo tee -a /etc/fstab > /dev/null <<EOF
${HEADNODE_IP}:/nfs   /nfs   nfs   defaults,_netdev,rsize=1048576,wsize=1048576,hard,timeo=600,retrans=2   0 0
${HEADNODE_IP}:/home  /home  nfs   defaults,_netdev,rsize=1048576,wsize=1048576,hard,timeo=600,retrans=2   0 0
EOF

# Test fstab
sudo umount /nfs /home
sudo mount -a
df -h | grep nfs
```

---

#### 4.8: Configure Munge Authentication

**[Concrete - critical for Slurm auth]**

```bash
# On your on-prem headnode, upload Munge key to AWS Secrets Manager
aws secretsmanager create-secret \
  --name slurm/munge-key \
  --description "Munge authentication key for Slurm cluster" \
  --secret-binary fileb:///etc/munge/munge.key \
  --region $AWS_REGION

# Note the ARN from output

# On AMI build instance, Munge key will be retrieved at boot time
# (Don't bake key into AMI - security risk)

# Prepare Munge directories
sudo mkdir -p /etc/munge /var/log/munge /var/lib/munge
sudo chown -R munge:munge /etc/munge /var/log/munge /var/lib/munge
sudo chmod 0700 /etc/munge /var/log/munge /var/lib/munge

# Enable Munge service
sudo systemctl enable munge

# Enable time sync (critical for Munge)
sudo systemctl enable chronyd
sudo systemctl start chronyd
```

---

#### 4.9: Configure Slurmd with IMDSv2 Support

**[Concrete - run on build instance]**

```bash
# Create script to get node name from EC2 tag using IMDSv2
sudo tee /usr/local/bin/get_slurm_nodename > /dev/null <<'EOF'
#!/bin/bash
# Get Slurm node name from EC2 instance Name tag using IMDSv2

# Get IMDSv2 token
TOKEN=$(curl -X PUT "http://169.254.169.254/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" \
    -s --max-time 2)

if [ -z "$TOKEN" ]; then
    echo "Error: Failed to retrieve IMDSv2 token" >&2
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

# Test it (will fail until instance has proper IAM role and tags)
/usr/local/bin/get_slurm_nodename
```

---

#### 4.10: Create Slurmd Systemd Service

**[Concrete - run on build instance]**

```bash
# Create slurmd.service
# ⚠️  Update SLURM_PREFIX if yours differs from /nfs/slurm
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

# Enable service (will start on boot)
sudo systemctl daemon-reload
sudo systemctl enable slurmd
```

---

#### 4.11: Create Boot Initialization Script

**[Concrete - runs on first boot of cloud instances]**

```bash
# This script retrieves Munge key and mounts NFS at boot
sudo tee /usr/local/bin/slurm-init.sh > /dev/null <<'EOFSCRIPT'
#!/bin/bash
# Slurm compute node initialization
# Runs on first boot to complete configuration

set -e

HEADNODE_IP="10.0.1.100"  # ⚠️  REPLACE with your headnode IP
NFS_EXPORT="/nfs"          # ⚠️  REPLACE with your NFS export
MUNGE_KEY_SECRET="slurm/munge-key"
AWS_REGION="us-east-1"     # ⚠️  REPLACE with your region

echo "$(date): Starting Slurm compute node initialization"

# Retrieve Munge key from AWS Secrets Manager
echo "Retrieving Munge key from Secrets Manager..."
aws secretsmanager get-secret-value \
  --secret-id $MUNGE_KEY_SECRET \
  --region $AWS_REGION \
  --query SecretBinary \
  --output text | base64 -d > /tmp/munge.key

if [ ! -s /tmp/munge.key ]; then
    echo "ERROR: Failed to retrieve Munge key"
    exit 1
fi

# Install Munge key
sudo mv /tmp/munge.key /etc/munge/munge.key
sudo chown munge:munge /etc/munge/munge.key
sudo chmod 600 /etc/munge/munge.key

# Start Munge
echo "Starting Munge..."
sudo systemctl start munge

# Test Munge (optional)
if ! munge -n | unmunge &>/dev/null; then
    echo "WARNING: Munge self-test failed"
fi

# Mount NFS (should already be in /etc/fstab, but ensure it's mounted)
echo "Mounting NFS..."
sudo mount -a

# Start slurmd (systemd will handle this, but ensure it's up)
echo "Starting slurmd..."
sudo systemctl start slurmd

echo "$(date): Slurm compute node initialization complete"
EOFSCRIPT

sudo chmod +x /usr/local/bin/slurm-init.sh
```

**Edit the script to match YOUR environment:**
```bash
sudo vi /usr/local/bin/slurm-init.sh
# Update HEADNODE_IP, NFS_EXPORT, AWS_REGION
```

---

#### 4.12: Clean Up and Prepare for AMI Creation

**[Concrete - run on build instance]**

```bash
# Stop services (will start fresh on instances launched from AMI)
sudo systemctl stop slurmd munge || true

# Clean up for AMI creation
sudo yum clean all
sudo rm -rf /tmp/* /var/tmp/*
sudo rm -f /root/.bash_history
sudo rm -f /home/ec2-user/.bash_history
sudo find /var/log -type f -exec truncate -s 0 {} \;

# Remove any Munge key (will be fetched at boot)
sudo rm -f /etc/munge/munge.key

# Remove any SSH host keys (will be regenerated)
sudo rm -f /etc/ssh/ssh_host_*

echo "AMI preparation complete. Ready to create image."
```

---

#### 4.13: Create AMI from Build Instance

**[Concrete - run from machine with AWS CLI]**

```bash
# Stop the build instance (required for consistent AMI)
aws ec2 stop-instances --instance-ids $BUILD_INSTANCE
aws ec2 wait instance-stopped --instance-ids $BUILD_INSTANCE

# Create AMI
SLURM_VERSION="20.02.3"  # Match what you built
SLURM_AMI=$(aws ec2 create-image \
  --instance-id $BUILD_INSTANCE \
  --name "slurm-compute-node-${SLURM_VERSION}-$(date +%Y%m%d-%H%M)" \
  --description "Slurm ${SLURM_VERSION} compute node for on-prem cloud bursting" \
  --tag-specifications "ResourceType=image,Tags=[{Key=Name,Value=slurm-compute-${SLURM_VERSION}},{Key=SlurmVersion,Value=${SLURM_VERSION}},{Key=Purpose,Value=CloudBursting}]" \
  --query 'ImageId' \
  --output text)

echo ""
echo "✅ AMI created: $SLURM_AMI"
echo ""
echo "Save this AMI ID - you'll use it in your launch template."
echo "Waiting for AMI to become available (this takes 5-10 minutes)..."

# Wait for AMI to be available
aws ec2 wait image-available --image-ids $SLURM_AMI

echo ""
echo "✅ AMI is ready!"
echo "AMI ID: $SLURM_AMI"
echo ""
echo "You can now terminate the build instance (optional):"
echo "  aws ec2 terminate-instances --instance-ids $BUILD_INSTANCE"
echo ""
echo "Proceed to Step 5: Configure AWS Resources"
```

**Save the AMI ID:**
```bash
export SLURM_AMI=ami-xxxxxxxxxxxxx  # Replace with your AMI ID
echo "export SLURM_AMI=$SLURM_AMI" >> ~/.bashrc
```

---

**🎉 Congratulations!** You now have an AMI with Slurm that exactly matches your on-prem cluster. This is the hardest part - it gets easier from here.

---

### Step 5: Configure AWS Infrastructure for Compute Nodes

**Goal**: Set up IAM roles, security groups, and launch templates for cloud compute instances

**Time estimate**: 30-45 minutes

#### 5.1: Create IAM Role for Compute Nodes

**[Concrete - do this]**

```bash
# Create trust policy (allows EC2 to assume this role)
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

# Create role
aws iam create-role \
  --role-name SlurmComputeNodeRole \
  --assume-role-policy-document file:///tmp/compute-trust-policy.json \
  --description "IAM role for Slurm cloud burst compute nodes"

# Create policy (needs to read EC2 tags + get Munge key from Secrets Manager)
cat > /tmp/compute-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeTags",
        "ec2:DescribeInstances"
      ],
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

# Attach policy to role
aws iam put-role-policy \
  --role-name SlurmComputeNodeRole \
  --policy-name ComputeNodePolicy \
  --policy-document file:///tmp/compute-policy.json

# Create instance profile (required to attach role to EC2 instances)
aws iam create-instance-profile \
  --instance-profile-name SlurmComputeNodeProfile

# Add role to instance profile
aws iam add-role-to-instance-profile \
  --instance-profile-name SlurmComputeNodeProfile \
  --role-name SlurmComputeNodeRole

# Wait for IAM propagation
echo "Waiting 10 seconds for IAM to propagate..."
sleep 10

echo "✅ IAM role created: SlurmComputeNodeRole"
```

---

#### 5.2: Create Security Group for Compute Nodes

**[Concrete - do this]**

```bash
# Create security group
SG_ID=$(aws ec2 create-security-group \
  --group-name slurm-compute-nodes \
  --description "Security group for Slurm burst compute nodes" \
  --vpc-id $VPC_ID \
  --tag-specifications 'ResourceType=security-group,Tags=[{Key=Name,Value=slurm-compute-sg}]' \
  --query 'GroupId' \
  --output text)

echo "Security Group ID: $SG_ID"

# Allow ALL traffic from on-prem network (simplest - adjust for your security posture)
ONPREM_CIDR="10.0.1.0/24"  # ⚠️  Your on-prem network CIDR
aws ec2 authorize-security-group-ingress \
  --group-id $SG_ID \
  --protocol all \
  --cidr $ONPREM_CIDR

# Allow ALL traffic within the security group (for multi-node jobs, MPI, etc.)
aws ec2 authorize-security-group-ingress \
  --group-id $SG_ID \
  --protocol all \
  --source-group $SG_ID

echo "✅ Security group configured"
```

> **📝 Security Note**: This allows ALL traffic from on-prem. For tighter security, allow only:
> - TCP 6817-6819 (slurmctld, slurmd, slurmdbd)
> - TCP/UDP 1024-65535 (ephemeral ports for Slurm)
> - TCP 2049 (NFS)
> - TCP 1011 (Munge)
> - ICMP (ping)

---

#### 5.3: Create Launch Template

**[Concrete - do this]**

```bash
# Get instance profile ARN
INSTANCE_PROFILE_ARN=$(aws iam get-instance-profile \
  --instance-profile-name SlurmComputeNodeProfile \
  --query 'InstanceProfile.Arn' \
  --output text)

# Create launch template
TEMPLATE_ID=$(aws ec2 create-launch-template \
  --launch-template-name slurm-burst-compute \
  --version-description "Slurm ${SLURM_VERSION} compute node for on-prem bursting" \
  --launch-template-data "{
    \"ImageId\": \"${SLURM_AMI}\",
    \"IamInstanceProfile\": {
      \"Arn\": \"${INSTANCE_PROFILE_ARN}\"
    },
    \"SecurityGroupIds\": [\"${SG_ID}\"],
    \"UserData\": \"$(echo '#!/bin/bash
/usr/local/bin/slurm-init.sh &> /var/log/slurm-init.log
' | base64 -w0)\",
    \"MetadataOptions\": {
      \"HttpTokens\": \"required\",
      \"HttpPutResponseHopLimit\": 1,
      \"InstanceMetadataTags\": \"enabled\"
    },
    \"TagSpecifications\": [{
      \"ResourceType\": \"instance\",
      \"Tags\": [
        {\"Key\": \"ManagedBy\", \"Value\": \"Slurm\"},
        {\"Key\": \"Environment\", \"Value\": \"CloudBurst\"},
        {\"Key\": \"SlurmVersion\", \"Value\": \"${SLURM_VERSION}\"}
      ]
    }]
  }" \
  --query 'LaunchTemplate.LaunchTemplateId' \
  --output text)

echo ""
echo "✅ Launch template created: $TEMPLATE_ID"
echo "Save this: export TEMPLATE_ID=$TEMPLATE_ID"
```

---

### Step 6: Install and Configure Plugin on On-Prem Headnode

**Goal**: Deploy plugin Python scripts on headnode that bridge Slurm and AWS

**Time estimate**: 20-30 minutes

#### 6.1: Install Plugin Files

**[Concrete - run on your on-prem headnode]**

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

echo "✅ Plugin files downloaded"
```

---

#### 6.2: Install Python Dependencies

**[Concrete - run on headnode]**

```bash
# Install Python 3 and pip
sudo yum install -y python3 python3-pip

# Install boto3 (AWS SDK for Python)
sudo pip3 install boto3 awscli

# Verify
python3 -c "import boto3; print('boto3 version:', boto3.__version__)"
aws --version
```

---

#### 6.3: Configure AWS Credentials on Headnode

**[Concrete - run on headnode]**

The headnode needs AWS credentials to launch/terminate instances.

**Option A: IAM Role (if headnode is EC2 instance)**

If your headnode is already in AWS:
```bash
# Attach IAM role to headnode instance (see docs/manual-installation.md for policy)
# No credentials file needed
```

**Option B: IAM User (typical for on-prem headnode)**

```bash
# Create IAM user for headnode
aws iam create-user --user-name slurm-headnode

# Attach policy (or use existing policy)
# See docs/configuration.md for minimal required permissions

# Create access keys
aws iam create-access-key --user-name slurm-headnode
# Save the AccessKeyId and SecretAccessKey

# Configure AWS CLI
sudo -u slurm aws configure
# Enter:
#   AWS Access Key ID: (from above)
#   AWS Secret Access Key: (from above)
#   Default region: us-east-1 (or your region)
#   Default output format: json

# Test
sudo -u slurm aws ec2 describe-instances --max-results 1
```

---

#### 6.4: Create Plugin Configuration Files

**[Concrete - run on headnode]**

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

echo "✅ config.json created"
```

**Configuration explained:**
- `LogLevel: INFO` - Logging verbosity (DEBUG for troubleshooting)
- `ResumeRate: 50` - Launch up to 50 nodes per minute
- `SuspendRate: 50` - Terminate up to 50 nodes per minute
- `ResumeTimeout: 600` - Wait 10 minutes for nodes to boot before marking DOWN
- `SuspendTime: 900` - Terminate nodes after 15 minutes idle

---

#### 6.5: Create Partition Configuration

**[Concrete - run on headnode]**

```bash
# Create partitions.json
# ⚠️  Update TEMPLATE_ID, SUBNET_ID, AWS_REGION from earlier steps
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

echo "✅ partitions.json created"
```

**Configuration explained:**
- `MaxNodes: 50` - Maximum 50 cloud nodes in this partition
- `CPUs: 4`, `RealMemory: 15000` - Slurm will see each node as 4 CPUs, 15 GB RAM
- `Weight: 100` - Scheduling weight (higher = prefer these nodes less)
- `PurchasingOption: on-demand` - Use On-Demand instances (change to "spot" for Spot)
- `InstanceType: c5.xlarge` - AWS instance type (4 vCPUs, 8 GB RAM)

> **📝 Customization**: Adjust instance type, MaxNodes, memory based on your needs. See [Configuration Reference](configuration.md) for all options.

---

### Step 7: Generate and Apply Slurm Configuration

**Goal**: Update slurm.conf to enable cloud bursting

**Time estimate**: 10-15 minutes

#### 7.1: Generate Cloud Node Configuration

**[Concrete - run on headnode]**

```bash
cd /etc/slurm/aws-plugin

# Generate slurm.conf fragment
sudo ./generate_conf.py

# Review generated configuration
cat slurm.conf.aws
```

Expected output:
```
# AWS Cloud Bursting Configuration
# Generated by aws-plugin-for-slurm

# Power Save Configuration
PrivateData=CLOUD
ResumeProgram=/etc/slurm/aws-plugin/resume.py
SuspendProgram=/etc/slurm/aws-plugin/suspend.py
ResumeRate=50
SuspendRate=50
ResumeTimeout=600
SuspendTime=900
TreeWidth=60000

# Cloud Partition
PartitionName=cloud Nodes=cloud-burst-[0-49] Default=No MaxTime=INFINITE State=UP
NodeName=cloud-burst-[0-49] CPUs=4 RealMemory=15000 Weight=100 State=CLOUD
```

---

#### 7.2: Backup and Update slurm.conf

**[Concrete - run on headnode]**

```bash
# Backup current slurm.conf
sudo cp /etc/slurm/slurm.conf /etc/slurm/slurm.conf.backup-$(date +%Y%m%d)

# Review the generated config one more time
cat /etc/slurm/aws-plugin/slurm.conf.aws

# If it looks good, append to main slurm.conf
sudo cat /etc/slurm/aws-plugin/slurm.conf.aws | sudo tee -a /etc/slurm/slurm.conf

# Verify (check that cloud partition is now in config)
grep -A 2 "PartitionName=cloud" /etc/slurm/slurm.conf
```

---

#### 7.3: Create Plugin Log Directory

**[Concrete - run on headnode]**

```bash
# Ensure log directory exists
sudo mkdir -p /var/log/slurm
sudo chown slurm:slurm /var/log/slurm
sudo chmod 755 /var/log/slurm

# Touch log file
sudo touch /var/log/slurm/aws_plugin.log
sudo chown slurm:slurm /var/log/slurm/aws_plugin.log
```

---

#### 7.4: Reconfigure Slurm

**[Concrete - run on headnode]**

```bash
# Test configuration syntax
sudo slurmctld -t
# Should output: slurm configuration is valid

# If syntax is valid, reconfigure live
sudo scontrol reconfigure

# Verify cloud partition is visible
sinfo
# You should see:
# PARTITION AVAIL  TIMELIMIT  NODES  STATE NODELIST
# cloud        up   infinite     50  idle~ cloud-burst-[0-49]
```

**What `idle~` means**: Nodes are powered down (in CLOUD state), ready to be resumed when jobs are submitted.

---

#### 7.5: Set Up Periodic Node State Management

**[Concrete - run on headnode]**

```bash
# Add cron job to clean up stuck nodes
sudo crontab -e

# Add this line (runs every minute):
* * * * * /etc/slurm/aws-plugin/change_state.py &>/dev/null
```

**What this does**: Periodically checks for nodes stuck in bad states (alloc#, down#, etc.) and attempts to clean them up.

---

### Step 8: Test Cloud Bursting

**Goal**: Submit test jobs and verify the full lifecycle works

**Time estimate**: 15-30 minutes

#### 8.1: Submit Simple Test Job

**[Concrete - run on headnode]**

```bash
# Submit job to cloud partition
srun -p cloud hostname

# What should happen:
# 1. Job enters queue
# 2. Node changes from 'idle~' to 'alloc#' (powering up)
# 3. Plugin launches EC2 instance
# 4. Instance boots, mounts NFS, starts slurmd
# 5. Plugin injects IP into Slurm: scontrol update NodeAddr=X.X.X.X
# 6. Node changes to 'alloc' (ready)
# 7. Job runs, outputs hostname
# 8. Node returns to 'idle' after job completes
# 9. After 900 seconds (SuspendTime), node returns to 'idle~' and instance terminates
```

---

#### 8.2: Monitor Progress

Open multiple terminal windows/tabs:

**Terminal 1 - Watch Slurm nodes:**
```bash
watch -n2 'sinfo -p cloud'
```

**Terminal 2 - Watch plugin logs:**
```bash
sudo tail -f /var/log/slurm/aws_plugin.log
```

**Terminal 3 - Watch AWS instances:**
```bash
watch -n5 'aws ec2 describe-instances \
  --filters "Name=tag:ManagedBy,Values=Slurm" \
            "Name=instance-state-name,Values=pending,running" \
  --query "Reservations[].Instances[].[InstanceId,State.Name,PrivateIpAddress,Tags[?Key==\`Name\`].Value|[0]]" \
  --output table'
```

**Terminal 4 - Submit job:**
```bash
srun -p cloud hostname
```

---

#### 8.3: Expected Timeline

| Time | Slurm State | AWS State | What's Happening |
|------|-------------|-----------|------------------|
| 0s | idle~ | (none) | Node powered down |
| 0s | alloc# | (none) | Job submitted, Slurm calls resume.py |
| 5s | alloc# | pending | resume.py created EC2 Fleet, instances launching |
| 30s | alloc# | running | Instance booted, running slurm-init.sh |
| 60s | alloc# | running | NFS mounted, Munge started, slurmd starting |
| 90s | alloc | running | resume.py injected IP, node joined cluster |
| 91s | alloc | running | Job running |
| 95s | idle | running | Job complete, node idle |
| 995s | idle~ | terminating | SuspendTime elapsed, suspend.py terminates instance |
| 1005s | idle~ | (none) | Instance terminated |

---

#### 8.4: Validate Successful Burst

**[Concrete - checks]**

```bash
# Check job completed successfully
scontrol show job <JOBID>
# Look for: JobState=COMPLETED ExitCode=0:0

# Check node is back in idle~ state
sinfo -p cloud
# Should show: cloud        up   infinite     50  idle~ cloud-burst-[0-49]

# Check instance was terminated
aws ec2 describe-instances \
  --filters "Name=tag:ManagedBy,Values=Slurm" \
  --query "Reservations[].Instances[].[InstanceId,State.Name,Tags[?Key=='Name'].Value|[0]]" \
  --output table
# Should show no running instances (or ones still in shutting-down)
```

---

#### 8.5: Test Multi-Node Job

**[Concrete - test scale]**

```bash
# Submit job that uses 10 nodes
srun -p cloud -N 10 hostname

# This will:
# - Launch 10 instances simultaneously
# - All mount NFS from on-prem
# - All join cluster
# - Job runs across all 10 nodes
# - All terminate after SuspendTime

# Monitor with sinfo:
watch sinfo -p cloud
# Should see: cloud up infinite 10  alloc# cloud-burst-[0-9]
```

---

#### 8.6: Test MPI Job (Optional)

**[Concrete - test inter-node communication]**

```bash
# Example MPI job
srun -p cloud -N 4 -n 16 mpirun -np 16 hostname

# This tests:
# - Multi-node launch
# - Security group allows inter-node traffic
# - MPI communication works over AWS network
```

---

### Step 9: Troubleshooting Common Issues

**If things don't work, check these:**

#### Issue: Nodes Stuck in `alloc#` State

**Symptoms**: Nodes never become `alloc`, stay `alloc#` forever, job never runs

**Causes**:
1. Instance failed to launch (check plugin logs)
2. Instance launched but slurmd didn't start
3. Network connectivity issues
4. NFS mount failed
5. Munge authentication failed

**Debug steps:**

```bash
# 1. Check plugin logs
sudo tail -50 /var/log/slurm/aws_plugin.log
# Look for errors like:
#   - "Failed to create EC2 Fleet" (AWS API issue)
#   - "No instances launched" (capacity issue, wrong subnet, etc.)

# 2. Check if instances actually launched
aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=cloud-burst-*" \
            "Name=instance-state-name,Values=pending,running,stopped" \
  --query "Reservations[].Instances[].[InstanceId,State.Name,StateReason.Message,PrivateIpAddress]" \
  --output table

# 3. If instance exists, check its system log
INSTANCE_ID=i-xxxxx  # From above
aws ec2 get-console-output --instance-id $INSTANCE_ID --output text
# Look for:
#   - NFS mount errors
#   - Munge key retrieval errors
#   - slurmd start errors

# 4. Check instance is reachable from headnode
ping <instance-private-ip>

# 5. If reachable, SSH in (if you added SSH key to launch template)
ssh -i slurm-builder.pem ec2-user@<instance-private-ip>
# Check:
sudo systemctl status munge
sudo systemctl status slurmd
mount | grep nfs
```

**Common fixes**:
- VPN down → Check tunnel status
- NFS mount fails → Check exports, firewall, security group
- Munge fails → Check key MD5 matches on-prem, check time sync (chronyc tracking)
- Slurmd doesn't start → Check /var/log/slurm/slurmd.log on instance

---

#### Issue: Nodes Don't Power Down

**Symptoms**: Nodes stay `idle`, instances never terminate

**Causes**:
1. suspend.py not being called
2. suspend.py can't find instances (Name tag mismatch)
3. IAM permissions issue

**Debug steps:**

```bash
# Check if suspend.py is in slurm.conf
grep SuspendProgram /etc/slurm/slurm.conf

# Manually trigger suspend
echo "cloud-burst-0" | sudo /etc/slurm/aws-plugin/suspend.py

# Check logs
sudo tail -20 /var/log/slurm/aws_plugin.log | grep suspend
```

---

#### Issue: NFS Mount Fails on Cloud Nodes

**Symptoms**: Instance console log shows "mount.nfs: Connection timed out"

**Causes**:
1. Headnode NFS not exported to AWS CIDR
2. Firewall blocking port 2049
3. Security group not allowing traffic from AWS subnet
4. Routing issue (VPN down)

**Debug steps:**

```bash
# On headnode, check exports
sudo exportfs -v | grep 10.1
# Should show: /nfs 10.1.0.0/16(rw,...)

# Check firewall
sudo firewall-cmd --list-all | grep nfs

# From a test instance in AWS, test connectivity
nc -zv 10.0.1.100 2049
showmount -e 10.0.1.100

# Try manual mount
sudo mount -t nfs -v 10.0.1.100:/nfs /mnt
```

---

#### Issue: Munge Authentication Fails

**Symptoms**: slurmd logs show "Munge authentication failed"

**Causes**:
1. Munge key mismatch
2. Time skew between on-prem and cloud
3. Munge not running

**Debug steps:**

```bash
# On headnode, get Munge key MD5
md5sum /etc/munge/munge.key

# On cloud instance, check Munge key MD5
md5sum /etc/munge/munge.key
# MUST match exactly

# Check time sync on cloud instance
chronyc tracking
# Time should be within ~5 minutes of headnode

# Test Munge locally
munge -n | unmunge

# Test Munge to headnode (from cloud instance)
munge -n | ssh headnode unmunge
```

---

## Production Considerations

### Cost Optimization

**Use Spot Instances for fault-tolerant workloads:**

```json
{
  "PurchasingOption": "spot",
  "SpotOptions": {
    "AllocationStrategy": "price-capacity-optimized",
    "MaxPrice": "0.50"
  }
}
```

**Right-size instances** based on actual usage:
```bash
# Check Slurm accounting for resource usage
sacct -X -o JobID,NodeList,ReqCPUS,ReqMem,MaxRSS,CPUTime,Elapsed
```

**Adjust SuspendTime** based on job patterns:
- Frequent short jobs → Longer SuspendTime (avoid launch overhead)
- Infrequent long jobs → Shorter SuspendTime (minimize idle cost)

---

### Monitoring and Alerts

Set up CloudWatch alarms:
- Instance launch failures
- High termination rate (may indicate issues)
- Cost thresholds

See [Monitoring Guide](monitoring.md) for details.

---

### Security Hardening

- Tighten security group rules (allow only required ports)
- Rotate AWS credentials regularly
- Use AWS Secrets Manager for Munge key (not S3)
- Enable CloudTrail for audit logging
- Consider using AWS Systems Manager Session Manager instead of SSH

See [Security Best Practices](security.md) for details.

---

## Summary: What You've Accomplished

✅ **Network connectivity** - VPN/Direct Connect between on-prem and AWS
✅ **User identity** - Consistent UIDs/GIDs across all nodes
✅ **Shared filesystem** - Cloud nodes can access on-prem storage
✅ **Custom AMI** - Slurm version matches on-prem exactly
✅ **AWS infrastructure** - IAM roles, security groups, launch templates configured
✅ **Plugin installed** - Headnode can launch/terminate cloud instances
✅ **Slurm configured** - Cloud bursting enabled, partition created
✅ **Tested** - Jobs successfully run on cloud nodes

**Your cluster can now:**
- Use local compute for regular workloads
- Automatically burst to AWS when local capacity is exhausted
- Terminate cloud nodes when no longer needed
- Pay only for cloud compute when actually used

---

## Next Steps

- **Tune performance**: [Performance Tuning Guide](performance-tuning.md)
- **Add monitoring**: [Monitoring Guide](monitoring.md)
- **Configure GPU bursting**: [Configuration Reference - GPU Configuration](configuration.md#gpu-configuration)
- **Set up Spot instances**: [Configuration Reference - SpotOptions](configuration.md#spotoptions)
- **Multi-region bursting**: [Advanced Usage - Multi-Region Deployments](advanced-usage.md#multi-region-deployments)

---

## Getting Help

If you encounter issues:

1. Check [Troubleshooting Guide](troubleshooting.md)
2. Review plugin logs: `/var/log/slurm/aws_plugin.log`
3. Use connectivity validator: `scripts/validate-onprem-connectivity.sh`
4. Open an issue: https://github.com/scttfrdmn/aws-plugin-for-slurm/issues

Include:
- Plugin version
- Slurm version (on-prem and AMI)
- Network setup (VPN or Direct Connect)
- Relevant log excerpts
