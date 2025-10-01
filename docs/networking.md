# Network Architecture Guide

This document details the network requirements, architecture patterns, and configuration for the AWS Plugin for Slurm.

## Overview

The plugin requires network connectivity between:
- Headnode and compute nodes (Slurm communication)
- Headnode and AWS APIs (instance management)
- Compute nodes and AWS APIs (instance metadata)
- Compute nodes and each other (optional, for MPI)

## Required Ports

### Slurm Ports

| Port | Protocol | Direction | Purpose | Required By |
|------|----------|-----------|---------|-------------|
| 6817 | TCP | Bidirectional | slurmctld (controller) | All nodes ↔ Headnode |
| 6818 | TCP | Bidirectional | slurmd (compute daemon) | Headnode ↔ Compute nodes |
| 6819 | TCP | Bidirectional | slurmdbd (database, optional) | Headnode ↔ Database |

### Support Services

| Port | Protocol | Direction | Purpose | Required By |
|------|----------|-----------|---------|-------------|
| 2049 | TCP | Inbound to Headnode | NFS | Compute nodes → Headnode |
| 6566 | TCP | Bidirectional | Munge authentication | All nodes ↔ All nodes |
| 22 | TCP | Inbound | SSH (management) | Admin → Headnode |
| 443 | TCP | Outbound | AWS APIs | All nodes → Internet |
| 123 | UDP | Outbound | NTP (time sync) | All nodes → Internet |

### MPI Communication (Optional)

| Port | Protocol | Direction | Purpose |
|------|----------|-----------|---------|
| All | TCP/UDP | Bidirectional | MPI job communication |

Most MPI implementations use dynamic port ranges. Allow all traffic between compute nodes.

## Network Topologies

### Topology 1: All Public Subnets

**Use case**: Simple development/testing

```
┌──────────────────────────────────────────────────┐
│                  VPC (10.0.0.0/16)                │
│                                                   │
│  ┌────────────────────────────────────────────┐  │
│  │  Public Subnet 1 (10.0.1.0/24) - AZ-a     │  │
│  │                                            │  │
│  │  ┌──────────────┐    ┌──────────────┐    │  │
│  │  │  Headnode    │    │ Compute Node │    │  │
│  │  │  Public IP   │    │  Public IP   │    │  │
│  │  └──────────────┘    └──────────────┘    │  │
│  └────────────────────────────────────────────┘  │
│                                                   │
│  ┌────────────────────────────────────────────┐  │
│  │  Public Subnet 2 (10.0.2.0/24) - AZ-b     │  │
│  │                                            │  │
│  │  ┌──────────────┐                         │  │
│  │  │ Compute Node │                         │  │
│  │  │  Public IP   │                         │  │
│  │  └──────────────┘                         │  │
│  └────────────────────────────────────────────┘  │
│                                                   │
│  Internet Gateway                                 │
└───────────────┬───────────────────────────────────┘
                │
            Internet
```

**Characteristics:**
- ✅ Simple configuration
- ✅ Direct internet access
- ✅ No NAT Gateway costs
- ❌ All nodes publicly accessible
- ❌ Security risk (must restrict SSH)
- ❌ Not recommended for production

**Security Group Rules:**

```bash
# Headnode SG
aws ec2 authorize-security-group-ingress \
  --group-id sg-headnode \
  --ip-permissions \
    IpProtocol=tcp,FromPort=22,ToPort=22,CidrIp=YOUR_IP/32 \
    IpProtocol=tcp,FromPort=6817,ToPort=6819,SourceSecurityGroupId=sg-compute \
    IpProtocol=tcp,FromPort=2049,ToPort=2049,SourceSecurityGroupId=sg-compute

# Compute SG
aws ec2 authorize-security-group-ingress \
  --group-id sg-compute \
  --ip-permissions \
    IpProtocol=tcp,FromPort=6818,ToPort=6818,SourceSecurityGroupId=sg-headnode \
    IpProtocol=-1,SourceSecurityGroupId=sg-compute
```

### Topology 2: Hybrid (Public Headnode, Private Compute)

**Use case**: Production deployments

```
┌──────────────────────────────────────────────────────┐
│                   VPC (10.0.0.0/16)                   │
│                                                       │
│  ┌─────────────────────────────────────────────────┐ │
│  │  Public Subnet (10.0.1.0/24) - AZ-a            │ │
│  │                                                 │ │
│  │  ┌──────────────┐        ┌────────────────┐   │ │
│  │  │  Headnode    │        │  NAT Gateway   │   │ │
│  │  │  Elastic IP  │        │  Elastic IP    │   │ │
│  │  └──────────────┘        └────────────────┘   │ │
│  └─────────────────────────────────────────────────┘ │
│                                   │                   │
│  ┌────────────────────────────────┼────────────────┐ │
│  │  Private Subnet 1 (10.0.10.0/24) - AZ-a       │ │
│  │                                │                │ │
│  │  ┌──────────────┐      ┌──────▼─────────┐    │ │
│  │  │ Compute Node │      │  Compute Node  │    │ │
│  │  │  No Public IP│      │  No Public IP  │    │ │
│  │  └──────────────┘      └────────────────┘    │ │
│  └─────────────────────────────────────────────── │ │
│                                                    │ │
│  ┌───────────────────────────────────────────────┐ │
│  │  Private Subnet 2 (10.0.20.0/24) - AZ-b      │ │
│  │                                ▲               │ │
│  │  ┌──────────────┐              │              │ │
│  │  │ Compute Node │──────────────┘              │ │
│  │  │  No Public IP│   (via NAT Gateway)         │ │
│  │  └──────────────┘                             │ │
│  └───────────────────────────────────────────────┘ │
│                                                     │
│  Internet Gateway                                   │
└──────────────┬──────────────────────────────────────┘
               │
           Internet
```

**Characteristics:**
- ✅ Compute nodes not internet-accessible
- ✅ Headnode accessible for management
- ✅ Compute nodes access internet via NAT
- ✅ Production-ready security
- ⚠️ NAT Gateway costs (~$32/month per AZ)
- ⚠️ NAT Gateway data transfer costs

**Route Tables:**

```bash
# Public subnet route table
0.0.0.0/0 → Internet Gateway

# Private subnet 1 route table
0.0.0.0/0 → NAT Gateway (in AZ-a)
10.0.0.0/16 → Local

# Private subnet 2 route table
0.0.0.0/0 → NAT Gateway (in AZ-a OR AZ-b for HA)
10.0.0.0/16 → Local
```

### Topology 3: All Private with VPC Endpoints

**Use case**: Maximum security, cost-optimized

```
┌──────────────────────────────────────────────────────┐
│                   VPC (10.0.0.0/16)                   │
│                                                       │
│  ┌─────────────────────────────────────────────────┐ │
│  │  Private Subnet 1 (10.0.10.0/24) - AZ-a        │ │
│  │                                                 │ │
│  │  ┌──────────────┐        ┌────────────────┐   │ │
│  │  │  Headnode    │        │  Compute Node  │   │ │
│  │  │  No Public IP│        │  No Public IP  │   │ │
│  │  └──────────────┘        └────────────────┘   │ │
│  └─────────────────────────────────────────────────┘ │
│                                                       │
│  ┌─────────────────────────────────────────────────┐ │
│  │  Private Subnet 2 (10.0.20.0/24) - AZ-b        │ │
│  │                                                 │ │
│  │  ┌──────────────┐                              │ │
│  │  │ Compute Node │                              │ │
│  │  │  No Public IP│                              │ │
│  │  └──────────────┘                              │ │
│  └─────────────────────────────────────────────────┘ │
│                                                       │
│  VPC Endpoints:                                       │
│  ┌─────────────────┐  ┌──────────────────┐          │
│  │ com.amazonaws.  │  │ com.amazonaws.   │          │
│  │ region.ec2      │  │ region.ssm       │          │
│  └─────────────────┘  └──────────────────┘          │
└───────────────────────────────────────────────────────┘
              ▲
              │ (Session Manager via VPC Endpoint)
        Management Access
```

**Characteristics:**
- ✅ Maximum security (no internet access)
- ✅ No NAT Gateway costs
- ✅ PrivateLink pricing (typically cheaper)
- ✅ AWS traffic stays on AWS backbone
- ⚠️ Requires VPC endpoints for all AWS services
- ⚠️ Access via Session Manager only (no SSH)

**Required VPC Endpoints:**

```bash
# EC2 endpoint (for CreateFleet, TerminateInstances, etc.)
aws ec2 create-vpc-endpoint \
  --vpc-id vpc-xxxxx \
  --service-name com.amazonaws.us-east-1.ec2 \
  --route-table-ids rtb-private1 rtb-private2

# SSM endpoints (for Session Manager)
for service in ssm ssmmessages ec2messages; do
  aws ec2 create-vpc-endpoint \
    --vpc-id vpc-xxxxx \
    --vpc-endpoint-type Interface \
    --service-name com.amazonaws.us-east-1.$service \
    --subnet-ids subnet-private1 subnet-private2 \
    --security-group-ids sg-endpoints
done
```

**VPC Endpoint Security Group:**

```bash
# Allow HTTPS from VPC
aws ec2 authorize-security-group-ingress \
  --group-id sg-endpoints \
  --ip-permissions IpProtocol=tcp,FromPort=443,ToPort=443,CidrIp=10.0.0.0/16
```

### Topology 4: Hybrid Cloud (On-Premises Headnode)

**Use case**: Bursting from on-premises to AWS

```
┌─────────────────────────────────────────────────────┐
│               On-Premises Data Center                │
│                                                      │
│  ┌────────────────┐        ┌──────────────┐        │
│  │   Headnode     │        │  Compute     │        │
│  │   slurmctld    │        │  Nodes       │        │
│  └────────────────┘        └──────────────┘        │
│           │                                         │
└───────────┼─────────────────────────────────────────┘
            │
    ┌───────▼────────┐
    │  VPN / Direct  │
    │    Connect     │
    └───────┬────────┘
            │
┌───────────▼─────────────────────────────────────────┐
│             AWS VPC (10.0.0.0/16)                    │
│                                                      │
│  ┌────────────────────────────────────────────────┐ │
│  │  Private Subnet 1 (10.0.10.0/24) - AZ-a       │ │
│  │                                                │ │
│  │  ┌──────────────┐        ┌────────────────┐  │ │
│  │  │ Compute Node │        │  Compute Node  │  │ │
│  │  │ (AWS Burst)  │        │  (AWS Burst)   │  │ │
│  │  └──────────────┘        └────────────────┘  │ │
│  └────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────┘
```

**Characteristics:**
- ✅ Leverage existing on-prem infrastructure
- ✅ Burst to AWS for peak workloads
- ✅ Keep data on-premises if required
- ⚠️ Requires VPN or Direct Connect
- ⚠️ Network latency between headnode and compute
- ⚠️ More complex configuration

**Network Requirements:**

1. **Site-to-Site VPN** or **Direct Connect** between on-premises and AWS VPC
2. **BGP routing** to advertise on-premises CIDR to AWS
3. **Security groups** allowing Slurm traffic from on-premises CIDR
4. **Low latency** (<50ms recommended)
5. **Stable connection** (for Munge time sensitivity)

**VPN Configuration:**

```bash
# Create customer gateway (on-premises endpoint)
aws ec2 create-customer-gateway \
  --type ipsec.1 \
  --public-ip YOUR_ONPREM_IP \
  --bgp-asn 65000

# Create VPN gateway
aws ec2 create-vpn-gateway --type ipsec.1

# Attach to VPC
aws ec2 attach-vpn-gateway \
  --vpn-gateway-id vgw-xxxxx \
  --vpc-id vpc-xxxxx

# Create VPN connection
aws ec2 create-vpn-connection \
  --type ipsec.1 \
  --customer-gateway-id cgw-xxxxx \
  --vpn-gateway-id vgw-xxxxx
```

## DNS Configuration

### Headnode DNS

**CloudFormation deployments** use private IP addresses, no DNS required.

**For production**, configure Route 53 private hosted zone:

```bash
# Create private hosted zone
aws route53 create-hosted-zone \
  --name slurm.internal \
  --vpc VPCRegion=us-east-1,VPCId=vpc-xxxxx \
  --caller-reference $(date +%s)

# Add headnode record
aws route53 change-resource-record-sets \
  --hosted-zone-id Z1234567890ABC \
  --change-batch '{
    "Changes": [{
      "Action": "UPSERT",
      "ResourceRecordSet": {
        "Name": "headnode.slurm.internal",
        "Type": "A",
        "TTL": 300,
        "ResourceRecords": [{"Value": "10.0.1.10"}]
      }
    }]
  }'
```

### Compute Node DNS

Compute nodes are transient and use dynamic names from instance tags. No DNS configuration required.

## NFS Configuration

### NFS Server (Headnode)

**Exports configuration** (`/etc/exports`):

```bash
/nfs *(rw,async,no_subtree_check,no_root_squash)
```

**Security considerations:**
- `no_root_squash` allows compute nodes root access (required for Slurm)
- Restrict to VPC CIDR in production:
  ```
  /nfs 10.0.0.0/16(rw,async,no_subtree_check,no_root_squash)
  ```

**Performance tuning**:

```bash
# /etc/nfs.conf
[nfsd]
threads=16
udp=n
vers3=n
vers4=y
vers4.0=y
vers4.1=y
vers4.2=y
```

### NFS Client (Compute Nodes)

**Mount options**:

```bash
mount -t nfs -o rw,async,hard,intr,vers=4.2 headnode:/nfs /nfs
```

**Options explanation:**
- `async` - Improves performance (data buffered before write)
- `hard` - Retry forever if server unavailable (prevents job failures)
- `intr` - Allow interrupts (Ctrl+C works)
- `vers=4.2` - Use NFSv4.2 (best performance)

### NFS Alternatives

#### Amazon EFS

**Pros:**
- Fully managed
- Multi-AZ redundancy
- Encryption at rest and in transit
- Scales automatically

**Cons:**
- Higher cost than self-managed NFS
- Slightly higher latency

**Configuration:**

```bash
# Create EFS filesystem
aws efs create-file-system \
  --creation-token slurm-shared \
  --performance-mode generalPurpose \
  --throughput-mode bursting \
  --encrypted

# Create mount targets in each AZ
aws efs create-mount-target \
  --file-system-id fs-xxxxx \
  --subnet-id subnet-private1 \
  --security-groups sg-efs
```

#### FSx for Lustre

**Pros:**
- High performance (100s of GB/s)
- S3 integration
- Optimized for HPC

**Cons:**
- More expensive
- Overkill for small clusters

**Use case:** Large-scale HPC with high I/O requirements

## IP Address Planning

### VPC CIDR Block

**Recommendations:**

| Cluster Size | VPC CIDR | Usable IPs | Example |
|--------------|----------|------------|---------|
| Small (< 100 nodes) | /24 | 251 | 10.0.1.0/24 |
| Medium (< 1000 nodes) | /20 | 4091 | 10.0.0.0/20 |
| Large (< 4000 nodes) | /18 | 16379 | 10.0.0.0/18 |
| Very Large | /16 | 65531 | 10.0.0.0/16 |

**Reserved IPs:**
- First 4 IPs in each subnet (AWS reserved)
- Last IP in each subnet (AWS reserved)
- Headnode static IP
- NAT Gateway IP (if used)

### Subnet Sizing

**Example for 1000-node cluster in /20 VPC:**

| Subnet | CIDR | Purpose | Usable IPs |
|--------|------|---------|------------|
| Public | 10.0.0.0/24 | Headnode, NAT GW | 251 |
| Private-1 | 10.0.4.0/22 | Compute (AZ-a) | 1019 |
| Private-2 | 10.0.8.0/22 | Compute (AZ-b) | 1019 |
| Reserved | 10.0.12.0/22 | Future use | 1019 |

## Bandwidth and Throughput

### Network Performance by Instance Type

| Instance Type | Network Bandwidth | Baseline/Burst | Use Case |
|---------------|-------------------|----------------|----------|
| t3.micro | Up to 5 Gbps | Burst only | Testing |
| t3.medium | Up to 5 Gbps | Burst only | Light workload |
| c5.large | Up to 10 Gbps | Burst to 10 | General compute |
| c5.xlarge | Up to 10 Gbps | Burst to 10 | Medium compute |
| c5.4xlarge | Up to 10 Gbps | 10 Gbps sustained | Heavy compute |
| c5n.18xlarge | 100 Gbps | 100 Gbps sustained | HPC, MPI |

### Placement Groups

For MPI workloads requiring low latency:

**Create placement group:**

```bash
aws ec2 create-placement-group \
  --group-name slurm-hpc \
  --strategy cluster
```

**Add to launch template:**

```json
{
  "Placement": {
    "GroupName": "slurm-hpc"
  }
}
```

**Limitations:**
- Single AZ only
- Limited instance types
- May encounter capacity issues

### Enhanced Networking

Ensure enhanced networking is enabled (ENA):

```bash
# Check if ENA is enabled
aws ec2 describe-instance-attribute \
  --instance-id i-xxxxx \
  --attribute enaSupport

# Enable in launch template (usually enabled by default)
{
  "NetworkInterfaces": [{
    "EnableEnaSupport": true
  }]
}
```

## Troubleshooting Network Issues

### Verify Connectivity

**From headnode to compute node:**

```bash
# Check Slurm connectivity
telnet 10.0.10.5 6818

# Check NFS
showmount -e headnode

# Check Munge
ssh compute-node "munge -n | unmunge"
```

**From compute node to headnode:**

```bash
# Check slurmctld
telnet headnode 6817

# Check NFS mount
mount | grep nfs

# Check Munge
systemctl status munge
```

### Common Issues

#### Nodes Don't Join Cluster

**Check:**
1. Security groups allow Slurm ports
2. Route tables configured correctly
3. NFS mount successful
4. Time synchronized (Munge requirement)

```bash
# Check time sync
chronyc tracking

# Force sync
chronyc makestep
```

#### High Latency

**Diagnose:**

```bash
# Ping test
ping -c 10 headnode

# MTU test (should be 9001 for jumbo frames)
ping -M do -s 8973 headnode

# Traceroute
traceroute headnode
```

**Solutions:**
- Use placement groups
- Enable jumbo frames (MTU 9001)
- Check for NAT Gateway bottleneck
- Use enhanced networking instances

#### NFS Performance Issues

**Check NFS stats:**

```bash
nfsstat -m
```

**Optimize:**

```bash
# Increase rsize/wsize
mount -o remount,rsize=1048576,wsize=1048576 /nfs

# Use multiple NFS threads
echo 16 > /proc/sys/fs/nfs/nfs_callback_threads
```

## Best Practices

1. **Use multiple AZs** for compute nodes (redundancy)
2. **Private subnets** for compute nodes (security)
3. **VPC endpoints** when possible (cost, security)
4. **Jumbo frames** (MTU 9001) for better performance
5. **Placement groups** for MPI workloads
6. **Enhanced networking** for high throughput
7. **Route 53** for DNS management
8. **CloudWatch** for network monitoring
9. **VPC Flow Logs** for troubleshooting
10. **Security groups** over NACLs (stateful is easier)

## Network Monitoring

### CloudWatch Metrics

Monitor these metrics:
- `NetworkIn` / `NetworkOut` (bytes)
- `NetworkPacketsIn` / `NetworkPacketsOut`
- `NetworkPerformanceBytes` (ENA instances)

### VPC Flow Logs

Enable for troubleshooting:

```bash
aws ec2 create-flow-logs \
  --resource-type VPC \
  --resource-ids vpc-xxxxx \
  --traffic-type ALL \
  --log-destination-type cloud-watch-logs \
  --log-group-name /aws/vpc/slurm
```

### Analysis

Query with CloudWatch Logs Insights:

```sql
fields @timestamp, srcAddr, dstAddr, srcPort, dstPort, protocol, bytes
| filter dstPort = 6818
| stats sum(bytes) as totalBytes by srcAddr
| sort totalBytes desc
```

## Next Steps

- Review [Security Best Practices](security.md)
- Configure [Monitoring](monitoring.md)
- Optimize [Performance](performance-tuning.md)
