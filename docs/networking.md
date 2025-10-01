# Network Architecture Guide

Network connectivity is the foundation of cloud bursting. This guide covers network requirements, architecture patterns, and tunnel options for connecting your on-premises Slurm cluster to AWS.

## Overview

The plugin requires **bi-directional routing** between:
- **On-prem headnode ↔ AWS compute nodes**: Slurm control messages (slurmctld ↔ slurmd)
- **On-prem headnode → AWS API**: Instance management (CreateFleet, TerminateInstances)
- **AWS compute nodes → On-prem headnode**: NFS mounts, Munge authentication
- **AWS compute nodes → AWS API**: Instance metadata (IMDSv2)
- **AWS compute nodes ↔ Each other**: MPI communication (optional)

**Key Requirement**: Your on-premises headnode must be able to reach AWS compute instances at their **private IP addresses**, and vice versa. This requires a network tunnel (VPN, Direct Connect, GRE, etc.) or running everything in AWS.

---

## Required Ports and Protocols

### Slurm Communication

| Port | Protocol | Direction | Purpose | Required By |
|------|----------|-----------|---------|-------------|
| 6817 | TCP | Bidirectional | slurmctld (controller) | Headnode ↔ All compute nodes |
| 6818 | TCP | Bidirectional | slurmd (daemon) | Headnode ↔ All compute nodes |
| 6819 | TCP | Optional | slurmdbd (database) | Headnode ↔ DB server |

### Support Services

| Port | Protocol | Direction | Purpose | Required By |
|------|----------|-----------|---------|-------------|
| 2049 | TCP/UDP | Compute → Headnode | NFS | Compute nodes → On-prem NFS server |
| 111 | TCP/UDP | Compute → Headnode | RPC (NFS) | Compute nodes → On-prem NFS server |
| 1011 | TCP/UDP | Bidirectional | Munge authentication | All nodes ↔ All nodes |
| 22 | TCP | Inbound | SSH (management) | Admin → Headnode |
| 443 | TCP | Outbound | AWS APIs (HTTPS) | Headnode + Compute → AWS |
| 123 | UDP | Outbound | NTP (time sync) | All nodes → Time server |

### MPI Communication (Optional)

| Port | Protocol | Direction | Purpose |
|------|----------|-----------|---------|
| Ephemeral | TCP/UDP | Bidirectional | MPI job communication |

Most MPI implementations use dynamic port ranges. **Best practice**: Allow all traffic between compute nodes within the same security group.

---

## Connectivity Options for On-Prem → AWS

### Quick Comparison

| Option | Setup Time | Monthly Cost | Bandwidth | Latency | Best For |
|--------|------------|--------------|-----------|---------|----------|
| **Site-to-Site VPN** | 30-60 min | ~$36 + data | 1.25 Gbps/tunnel | Variable (Internet) | Testing, light workloads |
| **Direct Connect** | Days-weeks | $200-1000+ | 1-100 Gbps | <10ms (dedicated) | Production, heavy I/O |
| **GRE Tunnel** | 1-2 hours | ~$0 (DIY) | Varies | Variable | Budget, control |
| **WireGuard** | 30-60 min | ~$0 (DIY) | 1-10 Gbps | Low overhead | Modern, simple |
| **Transit Gateway + VPN** | 2-4 hours | ~$100 + data | Multi-tunnel | Variable | Multi-VPC, complex |

---

### Option 1: AWS Site-to-Site VPN (Recommended for Getting Started)

**Best for**: Testing, proof-of-concept, light production workloads

#### Architecture

```
┌─────────────────────────────────┐         ┌─────────────────────────────────┐
│  On-Premises (10.0.1.0/24)      │         │  AWS VPC (10.1.0.0/16)          │
│                                 │         │                                 │
│  ┌──────────────────┐           │         │  ┌──────────────────┐           │
│  │  Slurm Headnode  │           │         │  │ Burst Compute    │           │
│  │  10.0.1.100      │◄──────────┼─────────┼─►│ 10.1.1.50        │           │
│  │                  │   IPsec   │         │  │                  │           │
│  └──────────────────┘   VPN     │         │  └──────────────────┘           │
│           │                     │         │                                 │
│  ┌────────▼────────┐            │         │  Virtual Private Gateway        │
│  │  VPN Endpoint   │            │         │  (vgw-xxxxx)                    │
│  │  203.0.113.50   │◄───────────┼─────────┼──────────────────────┐          │
│  │  (Router/FW)    │            │         │                      │          │
│  └─────────────────┘            │         │                      │          │
│                                 │         │  Customer Gateway    │          │
└─────────────────────────────────┘         │  (cgw-xxxxx)         │          │
                                            └──────────────────────┼──────────┘
                                                                   │
                                                            Internet Gateway
```

#### Pros & Cons

**Pros**:
- ✅ Fast setup (~30 minutes)
- ✅ AWS-managed infrastructure
- ✅ Automatic failover (2 tunnels)
- ✅ Low monthly cost (~$36/month)
- ✅ No special hardware required

**Cons**:
- ❌ Internet-based (variable latency)
- ❌ 1.25 Gbps per tunnel limit
- ❌ Data transfer costs ($0.09/GB out)
- ❌ Latency typically 20-100ms

#### AWS Setup

```bash
# 1. Create Customer Gateway (your on-prem public IP)
ONPREM_PUBLIC_IP="203.0.113.50"  # Your router/firewall public IP

CGW_ID=$(aws ec2 create-customer-gateway \
  --type ipsec.1 \
  --public-ip $ONPREM_PUBLIC_IP \
  --bgp-asn 65000 \
  --tag-specifications 'ResourceType=customer-gateway,Tags=[{Key=Name,Value=onprem-cgw}]' \
  --query 'CustomerGateway.CustomerGatewayId' \
  --output text)

echo "Customer Gateway: $CGW_ID"

# 2. Create Virtual Private Gateway
VGW_ID=$(aws ec2 create-vpn-gateway \
  --type ipsec.1 \
  --amazon-side-asn 64512 \
  --tag-specifications 'ResourceType=vpn-gateway,Tags=[{Key=Name,Value=slurm-vgw}]' \
  --query 'VpnGateway.VpnGatewayId' \
  --output text)

echo "VPN Gateway: $VGW_ID"

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
  --options "StaticRoutesOnly=false,TunnelOptions=[{PreSharedKey=MyStrongPSK123456789},{PreSharedKey=MyStrongPSK987654321}]" \
  --tag-specifications 'ResourceType=vpn-connection,Tags=[{Key=Name,Value=slurm-vpn}]' \
  --query 'VpnConnection.VpnConnectionId' \
  --output text)

echo "VPN Connection: $VPN_ID"

# 5. Enable route propagation in VPC
ROUTE_TABLE_ID=$(aws ec2 describe-route-tables \
  --filters "Name=vpc-id,Values=$VPC_ID" "Name=association.main,Values=true" \
  --query 'RouteTables[0].RouteTableId' \
  --output text)

aws ec2 enable-vgw-route-propagation \
  --route-table-id $ROUTE_TABLE_ID \
  --gateway-id $VGW_ID

# 6. Download VPN configuration for your router
aws ec2 describe-vpn-connections \
  --vpn-connection-ids $VPN_ID \
  --query 'VpnConnections[0].CustomerGatewayConfiguration' \
  --output text > vpn-config.xml

echo "VPN config saved to vpn-config.xml"
```

#### On-Premises Setup (Examples)

<details>
<summary><b>strongSwan (Linux)</b></summary>

```bash
# Install strongSwan
sudo yum install -y strongswan  # RHEL/CentOS
# sudo apt install -y strongswan  # Debian/Ubuntu

# Extract values from vpn-config.xml
AWS_TUNNEL1_IP="52.1.2.3"     # From vpn-config.xml
AWS_TUNNEL2_IP="52.4.5.6"     # From vpn-config.xml
ONPREM_PRIVATE_CIDR="10.0.1.0/24"
AWS_VPC_CIDR="10.1.0.0/16"
PSK1="MyStrongPSK123456789"
PSK2="MyStrongPSK987654321"

# Configure /etc/strongswan/ipsec.conf
sudo tee /etc/strongswan/ipsec.conf > /dev/null <<EOF
config setup
    charondebug="ike 2, knl 2, cfg 2, net 2"
    uniqueids=no

conn aws-tunnel1
    auto=start
    left=%defaultroute
    leftid=$ONPREM_PUBLIC_IP
    leftsubnet=$ONPREM_PRIVATE_CIDR
    right=$AWS_TUNNEL1_IP
    rightsubnet=$AWS_VPC_CIDR
    type=tunnel
    ikelifetime=28800s
    lifetime=3600s
    margintime=540s
    keyexchange=ikev1
    authby=secret
    keyingtries=%forever
    ike=aes128-sha1-modp1024!
    esp=aes128-sha1-modp1024!
    dpdaction=restart
    dpddelay=10s
    dpdtimeout=30s

conn aws-tunnel2
    also=aws-tunnel1
    right=$AWS_TUNNEL2_IP
EOF

# Configure pre-shared keys
sudo tee /etc/strongswan/ipsec.secrets > /dev/null <<EOF
$ONPREM_PUBLIC_IP $AWS_TUNNEL1_IP : PSK "$PSK1"
$ONPREM_PUBLIC_IP $AWS_TUNNEL2_IP : PSK "$PSK2"
EOF

sudo chmod 600 /etc/strongswan/ipsec.secrets

# Enable IP forwarding
echo "net.ipv4.ip_forward = 1" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p

# Start strongSwan
sudo systemctl enable strongswan
sudo systemctl start strongswan

# Check status
sudo strongswan status
```
</details>

<details>
<summary><b>pfSense / OPNsense</b></summary>

1. **VPN > IPsec > Tunnels > Add P1**
   - Remote Gateway: `52.1.2.3` (AWS Tunnel 1 IP)
   - Key Exchange: IKEv1
   - Encryption: AES 128-bit
   - Hash: SHA1
   - DH Group: 2 (1024 bit)
   - Lifetime: 28800 seconds
   - Pre-Shared Key: `MyStrongPSK123456789`

2. **Add P2 (Phase 2)**
   - Mode: Tunnel IPv4
   - Local Network: Your on-prem CIDR (10.0.1.0/24)
   - Remote Network: AWS VPC CIDR (10.1.0.0/16)
   - Protocol: ESP
   - Encryption: AES 128
   - Hash: SHA1
   - PFS Group: None
   - Lifetime: 3600 seconds

3. **Repeat for Tunnel 2** with different IP and PSK

4. **Firewall Rules > IPsec**
   - Allow all traffic from AWS VPC CIDR (10.1.0.0/16)

5. **Check Status > IPsec** for tunnel status
</details>

<details>
<summary><b>Cisco IOS</b></summary>

```cisco
! Configure IKE Phase 1
crypto isakmp policy 200
 encryption aes
 hash sha
 authentication pre-share
 group 2
 lifetime 28800

crypto isakmp key MyStrongPSK123456789 address 52.1.2.3

! Configure IPsec Phase 2
crypto ipsec transform-set ipsec-prop-vpn-aws esp-aes esp-sha-hmac
 mode tunnel

crypto ipsec df-bit clear

crypto ipsec security-association lifetime seconds 3600

! Configure ACL for interesting traffic
access-list 100 permit ip 10.0.1.0 0.0.0.255 10.1.0.0 0.0.255.255

! Configure crypto map
crypto map vpn-to-aws 200 ipsec-isakmp
 set peer 52.1.2.3
 set transform-set ipsec-prop-vpn-aws
 match address 100

! Apply to WAN interface
interface GigabitEthernet0/0
 crypto map vpn-to-aws

! Configure routing
ip route 10.1.0.0 255.255.0.0 Tunnel0
```
</details>

#### Routing Configuration

**On-premises side**: Add static route to AWS VPC

```bash
# Linux (temporary)
sudo ip route add 10.1.0.0/16 dev ipsec0

# Or via VPN tunnel gateway
sudo ip route add 10.1.0.0/16 via 169.254.x.x  # Inside tunnel IP

# Persistent (add to network config)
# RHEL/CentOS: /etc/sysconfig/network-scripts/route-<interface>
# Debian/Ubuntu: /etc/network/interfaces or netplan config
```

**AWS side**: Automatic via route propagation (already configured above)

---

### Option 2: AWS Direct Connect (Production Workloads)

**Best for**: Production, heavy I/O, latency-sensitive workloads

#### Architecture

```
┌─────────────────────────────────┐
│  On-Premises Data Center        │
│                                 │         Direct Connect Location
│  ┌──────────────────┐           │         ┌─────────────────────┐
│  │  Slurm Headnode  │           │         │                     │
│  │  10.0.1.100      │◄──────────┼─────────┤  AWS Cage           │
│  │                  │  Dedicated│         │  Cross-Connect      │
│  └──────────────────┘  Fiber    │         │                     │
│           │                     │         └──────────┬──────────┘
│  ┌────────▼────────┐            │                    │
│  │  Router/Switch  │            │                    │
│  │                 │◄───────────┼────────────────────┘
│  └─────────────────┘            │         Private VLAN
│                                 │
└─────────────────────────────────┘
                                            ┌─────────────────────────────────┐
                                            │  AWS VPC (10.1.0.0/16)          │
                                            │                                 │
                                            │  ┌──────────────────┐           │
                                            │  │ Burst Compute    │           │
                                            │  │ 10.1.1.50        │           │
                                            │  └──────────────────┘           │
                                            │                                 │
                                            │  Virtual Private Gateway        │
                                            │  (with Direct Connect)          │
                                            └─────────────────────────────────┘
```

#### Pros & Cons

**Pros**:
- ✅ Dedicated bandwidth (1 Gbps - 100 Gbps)
- ✅ Consistent low latency (<10ms typical)
- ✅ Private connectivity (not Internet)
- ✅ Lower data transfer costs ($0.02/GB)
- ✅ Higher throughput for NFS/shared storage

**Cons**:
- ❌ Longer setup time (days to weeks)
- ❌ Higher monthly cost ($200-$1000+)
- ❌ Requires physical cross-connect
- ❌ More complex to configure

#### Setup Process

1. **Order Direct Connect**: Contact AWS or partner
2. **Choose location**: Closest DX location to your data center
3. **Select capacity**: 1 Gbps, 10 Gbps, or 100 Gbps
4. **Physical installation**: Cross-connect fiber in colocation
5. **Virtual interfaces**: Create private VIF for VPC access

```bash
# Create Virtual Private Gateway (if not already exists)
VGW_ID=$(aws ec2 create-vpn-gateway \
  --type ipsec.1 \
  --amazon-side-asn 64512)

aws ec2 attach-vpn-gateway \
  --vpc-id $VPC_ID \
  --vpn-gateway-id $VGW_ID

# Create Direct Connect Gateway (for multi-region or Transit Gateway)
DXGW_ID=$(aws directconnect create-direct-connect-gateway \
  --direct-connect-gateway-name slurm-dxgw \
  --amazon-side-asn 64512)

# Associate with VGW
aws directconnect create-direct-connect-gateway-association \
  --direct-connect-gateway-id $DXGW_ID \
  --virtual-gateway-id $VGW_ID

# Create Private VIF (your AWS account team will help with this)
# Requires: VLAN ID, BGP ASN, BGP peer IPs
```

**When to use Direct Connect**:
- Transferring > 1 TB/month
- Latency < 20ms required (Munge, MPI)
- Bandwidth > 1 Gbps sustained
- Multi-year commitment

---

### Option 3: GRE Tunnel (DIY, Budget-Friendly)

**Best for**: Full control, budget-conscious, existing GRE infrastructure

#### Architecture

```
┌─────────────────────────────────┐         ┌─────────────────────────────────┐
│  On-Premises (10.0.1.0/24)      │         │  AWS VPC (10.1.0.0/16)          │
│                                 │         │                                 │
│  ┌──────────────────┐           │         │  ┌──────────────────┐           │
│  │  Slurm Headnode  │           │         │  │ Burst Compute    │           │
│  │  10.0.1.100      │◄──────────┼─────────┼─►│ 10.1.1.50        │           │
│  └──────────────────┘           │         │  └──────────────────┘           │
│           │                     │         │           │                     │
│  ┌────────▼────────┐            │         │  ┌────────▼────────┐            │
│  │  Linux Router   │            │         │  │  EC2 Instance   │            │
│  │  GRE Endpoint   │◄───────────┼─────────┼─►│  GRE Endpoint   │            │
│  │  203.0.113.50   │   GRE over │         │  │  52.1.2.3       │            │
│  │                 │   Internet │         │  │  (t3.micro)     │            │
│  └─────────────────┘            │         │  └─────────────────┘            │
│                                 │         │                                 │
└─────────────────────────────────┘         └─────────────────────────────────┘
```

#### Pros & Cons

**Pros**:
- ✅ Very low cost (just EC2 instance ~$7/month)
- ✅ Full control over routing
- ✅ Fast setup (~1-2 hours)
- ✅ No vendor lock-in

**Cons**:
- ❌ Unencrypted (use with IPsec or trust network)
- ❌ Internet-based (variable latency)
- ❌ Single point of failure (EC2 instance)
- ❌ Requires Linux knowledge

#### Setup

**AWS side** (EC2 GRE endpoint):

```bash
# Launch small EC2 instance in public subnet
GRE_INSTANCE=$(aws ec2 run-instances \
  --image-id ami-0c55b159cbfafe1f0 \  # Amazon Linux 2
  --instance-type t3.micro \
  --subnet-id $PUBLIC_SUBNET_ID \
  --associate-public-ip-address \
  --security-group-ids $GRE_SG_ID \
  --key-name your-key \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=gre-endpoint}]' \
  --query 'Instances[0].InstanceId' \
  --output text)

# Disable source/dest check (required for routing)
aws ec2 modify-instance-attribute \
  --instance-id $GRE_INSTANCE \
  --no-source-dest-check

# Get its public IP
GRE_AWS_PUBLIC_IP=$(aws ec2 describe-instances \
  --instance-ids $GRE_INSTANCE \
  --query 'Reservations[0].Instances[0].PublicIpAddress' \
  --output text)

echo "AWS GRE endpoint: $GRE_AWS_PUBLIC_IP"

# SSH to instance and configure GRE
ssh -i your-key.pem ec2-user@$GRE_AWS_PUBLIC_IP

# On AWS GRE instance:
sudo modprobe ip_gre
sudo ip tunnel add gre1 mode gre remote 203.0.113.50 local $(hostname -I | awk '{print $1}') ttl 255
sudo ip addr add 192.168.100.2/30 dev gre1
sudo ip link set gre1 up

# Add route to on-prem network
sudo ip route add 10.0.1.0/24 dev gre1

# Enable forwarding
echo "net.ipv4.ip_forward = 1" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p

# Set up routing to AWS private subnet
sudo iptables -t nat -A POSTROUTING -s 10.0.1.0/24 -d 10.1.0.0/16 -j MASQUERADE
sudo iptables -A FORWARD -i gre1 -o eth0 -j ACCEPT
sudo iptables -A FORWARD -i eth0 -o gre1 -j ACCEPT
```

**On-premises side**:

```bash
# On your on-prem Linux router
sudo modprobe ip_gre
sudo ip tunnel add gre1 mode gre remote $GRE_AWS_PUBLIC_IP local 203.0.113.50 ttl 255
sudo ip addr add 192.168.100.1/30 dev gre1
sudo ip link set gre1 up

# Add route to AWS VPC
sudo ip route add 10.1.0.0/16 dev gre1

# Enable forwarding
echo "net.ipv4.ip_forward = 1" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

**Test**:
```bash
# From on-prem headnode
ping 192.168.100.2  # GRE tunnel
ping 10.1.1.50      # AWS instance (if running)
```

**Make persistent** (add to `/etc/network/interfaces` or systemd networkd)

---

### Option 4: WireGuard (Modern, Secure)

**Best for**: Modern setup, strong encryption, low overhead

#### Pros & Cons

**Pros**:
- ✅ Modern cryptography (Noise protocol)
- ✅ Low overhead (~10% vs OpenVPN ~25%)
- ✅ Simple configuration
- ✅ Fast (in-kernel)
- ✅ Free/open source

**Cons**:
- ❌ Requires kernel 5.6+ or DKMS
- ❌ Internet-based (variable latency)
- ❌ Manual setup (no AWS managed service)

#### Setup

**AWS side** (EC2 WireGuard endpoint):

```bash
# Launch EC2 instance
WG_INSTANCE=$(aws ec2 run-instances \
  --image-id ami-0c55b159cbfafe1f0 \
  --instance-type t3.micro \
  --subnet-id $PUBLIC_SUBNET_ID \
  --associate-public-ip-address \
  --security-group-ids $WG_SG_ID \
  --key-name your-key \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=wireguard-endpoint}]' \
  --query 'Instances[0].InstanceId' \
  --output text)

aws ec2 modify-instance-attribute \
  --instance-id $WG_INSTANCE \
  --no-source-dest-check

WG_AWS_PUBLIC_IP=$(aws ec2 describe-instances \
  --instance-ids $WG_INSTANCE \
  --query 'Reservations[0].Instances[0].PublicIpAddress' \
  --output text)

# SSH and install WireGuard
ssh -i your-key.pem ec2-user@$WG_AWS_PUBLIC_IP

# Install WireGuard
sudo yum install -y wireguard-tools
# Or: sudo amazon-linux-extras install -y wireguard-tools

# Generate keys
wg genkey | sudo tee /etc/wireguard/private.key
sudo chmod 600 /etc/wireguard/private.key
sudo cat /etc/wireguard/private.key | wg pubkey | sudo tee /etc/wireguard/public.key

# Configure WireGuard
sudo tee /etc/wireguard/wg0.conf > /dev/null <<EOF
[Interface]
Address = 192.168.200.1/24
ListenPort = 51820
PrivateKey = $(sudo cat /etc/wireguard/private.key)
PostUp = iptables -A FORWARD -i wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE

[Peer]
PublicKey = <ONPREM_PUBLIC_KEY>  # From on-prem setup
AllowedIPs = 10.0.1.0/24
PersistentKeepalive = 25
EOF

# Enable IP forwarding
echo "net.ipv4.ip_forward = 1" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p

# Start WireGuard
sudo systemctl enable wg-quick@wg0
sudo systemctl start wg-quick@wg0

# Show public key (copy for on-prem config)
sudo cat /etc/wireguard/public.key
```

**On-premises side**:

```bash
# Install WireGuard
# Debian/Ubuntu
sudo apt install -y wireguard

# RHEL 8/Rocky/Alma
sudo dnf install -y wireguard-tools

# Generate keys
wg genkey | sudo tee /etc/wireguard/private.key
sudo chmod 600 /etc/wireguard/private.key
sudo cat /etc/wireguard/private.key | wg pubkey | sudo tee /etc/wireguard/public.key

# Configure
sudo tee /etc/wireguard/wg0.conf > /dev/null <<EOF
[Interface]
Address = 192.168.200.2/24
PrivateKey = $(sudo cat /etc/wireguard/private.key)

[Peer]
PublicKey = <AWS_PUBLIC_KEY>  # From AWS setup
Endpoint = $WG_AWS_PUBLIC_IP:51820
AllowedIPs = 10.1.0.0/16, 192.168.200.1/32
PersistentKeepalive = 25
EOF

# Start WireGuard
sudo systemctl enable wg-quick@wg0
sudo systemctl start wg-quick@wg0

# Test
ping 192.168.200.1
```

**Security group** (AWS):
```bash
# Allow WireGuard port
aws ec2 authorize-security-group-ingress \
  --group-id $WG_SG_ID \
  --protocol udp \
  --port 51820 \
  --cidr 0.0.0.0/0
```

---

### Option 5: Transit Gateway + Multiple VPNs (Enterprise)

**Best for**: Multi-VPC, multi-region, or complex hub-and-spoke

#### Architecture

```
┌─────────────────────────┐
│  On-Premises            │
│                         │         ┌──────────────────────────────────┐
│  ┌──────────────────┐   │         │  AWS Transit Gateway             │
│  │  Slurm Headnode  │   │         │                                  │
│  │  10.0.1.100      │◄──┼─────────┤  Hub for VPCs                    │
│  └──────────────────┘   │  VPN1   │                                  │
│           │             │         │  ┌────────────┐  ┌────────────┐  │
│  ┌────────▼────────┐    │         │  │ VPC 1      │  │ VPC 2      │  │
│  │  VPN Endpoint   │◄───┼─────────┼──│ 10.1.0/16  │  │ 10.2.0/16  │  │
│  │  203.0.113.50   │    │  VPN2   │  └────────────┘  └────────────┘  │
│  └─────────────────┘    │         │  ┌────────────┐                  │
│                         │         │  │ VPC 3      │                  │
└─────────────────────────┘         │  │ 10.3.0/16  │                  │
                                    │  └────────────┘                  │
                                    └──────────────────────────────────┘
```

**Benefits**:
- Centralized routing
- Connect multiple VPCs easily
- Multi-region support
- ECMP (Equal Cost Multi-Path) for VPN bandwidth scaling

**Cost**: ~$36/month (VPN) + $50/month (TGW) + $0.02/GB

---

## Bi-Directional Routing Requirements

### What Must Route

| Source | Destination | Protocol/Port | Purpose |
|--------|-------------|---------------|---------|
| On-prem headnode | AWS compute private IPs | TCP 6818 | slurmd communication |
| AWS compute nodes | On-prem headnode | TCP 6817 | slurmctld communication |
| AWS compute nodes | On-prem NFS server | TCP/UDP 2049, 111 | NFS mounts |
| AWS compute nodes | On-prem headnode | TCP/UDP 1011 | Munge auth |
| On-prem headnode | AWS EC2 API endpoints | TCP 443 | Instance management |
| AWS compute nodes | AWS EC2 metadata | TCP 80/443 | IMDSv2 |

### Routing Table Examples

**On-premises route table**:
```
Destination         Gateway
10.1.0.0/16         VPN/DX/GRE tunnel
0.0.0.0/0           Internet gateway
```

**AWS VPC route table**:
```
Destination         Target
10.1.0.0/16         local
10.0.1.0/24         vgw-xxxxx (VPN gateway)
0.0.0.0/0           igw-xxxxx or nat-xxxxx
```

### Testing Bi-Directional Routing

```bash
# From on-prem headnode → AWS compute
ping 10.1.1.50
nc -zv 10.1.1.50 6818

# From AWS compute → On-prem headnode
ping 10.0.1.100
nc -zv 10.0.1.100 6817
nc -zv 10.0.1.100 2049  # NFS
```

---

## All-AWS Deployment Topologies

If running everything in AWS (headnode + compute in same VPC), networking is simpler:

### Topology 1: Public Headnode, Private Compute (Recommended)

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
│  │  └──────────────┘      └────────────────┘    │ │
│  └─────────────────────────────────────────────── │ │
│                                                    │ │
│  ┌───────────────────────────────────────────────┐ │
│  │  Private Subnet 2 (10.0.20.0/24) - AZ-b      │ │
│  │  ┌──────────────┐                             │ │
│  │  │ Compute Node │                             │ │
│  │  └──────────────┘                             │ │
│  └───────────────────────────────────────────────┘ │
│                                                     │
│  Internet Gateway                                   │
└──────────────┬──────────────────────────────────────┘
               │
           Internet
```

**Route tables**:
- Public subnet: `0.0.0.0/0 → igw-xxxxx`
- Private subnets: `0.0.0.0/0 → nat-xxxxx`, `10.0.0.0/16 → local`

---

## Security Groups

### Headnode Security Group

```bash
# Inbound
aws ec2 authorize-security-group-ingress \
  --group-id sg-headnode \
  --ip-permissions \
    IpProtocol=tcp,FromPort=22,ToPort=22,CidrIp=YOUR_IP/32 \
    IpProtocol=tcp,FromPort=6817,ToPort=6819,SourceSecurityGroupId=sg-compute \
    IpProtocol=tcp,FromPort=2049,ToPort=2049,SourceSecurityGroupId=sg-compute \
    IpProtocol=tcp,FromPort=111,ToPort=111,SourceSecurityGroupId=sg-compute \
    IpProtocol=udp,FromPort=111,ToPort=111,SourceSecurityGroupId=sg-compute \
    IpProtocol=tcp,FromPort=1011,ToPort=1011,SourceSecurityGroupId=sg-compute

# Outbound: All (default)
```

### Compute Node Security Group

```bash
# Inbound
aws ec2 authorize-security-group-ingress \
  --group-id sg-compute \
  --ip-permissions \
    IpProtocol=tcp,FromPort=6818,ToPort=6818,SourceSecurityGroupId=sg-headnode \
    IpProtocol=-1,SourceSecurityGroupId=sg-compute  # Inter-node (MPI)

# For on-prem bursting, also allow from on-prem CIDR:
aws ec2 authorize-security-group-ingress \
  --group-id sg-compute \
  --ip-permissions \
    IpProtocol=tcp,FromPort=6818,ToPort=6818,CidrIp=10.0.1.0/24 \
    IpProtocol=-1,CidrIp=10.0.1.0/24

# Outbound: All (default)
```

---

## DNS Configuration

### For On-Prem Bursting

**DNS is NOT required** - the plugin injects private IPs directly via `scontrol update NodeAddr=<IP>`.

**Optional**: Use Route 53 Private Hosted Zone for friendly names:

```bash
# Create private hosted zone (associated with AWS VPC)
aws route53 create-hosted-zone \
  --name slurm.internal \
  --vpc VPCRegion=us-east-1,VPCId=$VPC_ID \
  --caller-reference $(date +%s)

# Add headnode A record (on-prem IP)
aws route53 change-resource-record-sets \
  --hosted-zone-id Z1234567890ABC \
  --change-batch '{
    "Changes": [{
      "Action": "UPSERT",
      "ResourceRecordSet": {
        "Name": "headnode.slurm.internal",
        "Type": "A",
        "TTL": 300,
        "ResourceRecords": [{"Value": "10.0.1.100"}]
      }
    }]
  }'
```

Then in `slurm.conf`:
```
SlurmctldHost=headnode.slurm.internal
```

---

## NFS Over WAN Considerations

When using NFS from on-prem to AWS over VPN/DX:

### Mount Options for WAN

```bash
# Recommended mount options for WAN NFS
mount -t nfs -o \
  rsize=1048576,wsize=1048576,\
  hard,timeo=600,retrans=2,\
  _netdev,\
  noresvport,\
  vers=4.1 \
  10.0.1.100:/nfs /nfs
```

**Options explained**:
- `rsize/wsize=1048576`: Larger buffers (1 MB) for better throughput
- `hard,timeo=600,retrans=2`: Retry longer over WAN
- `_netdev`: Wait for network before mounting
- `noresvport`: Don't use reserved ports (helps with NAT)
- `vers=4.1`: Use NFSv4.1 (better over WAN than v3)

### NFS Alternatives for WAN

| Option | Pros | Cons | Best For |
|--------|------|------|----------|
| **NFS from on-prem** | Simple, existing | Latency-sensitive | Light I/O |
| **Amazon EFS** | Managed, low latency in AWS | Data must sync, cost | Cloud-native |
| **FSx for Lustre** | Very high performance | Expensive, complex | Heavy HPC |
| **S3 via goofys/s3fs** | Cheap, simple | Limited POSIX, slow | Read-mostly |
| **Replicate with rsync** | Simple | Manual sync, stale data | Infrequent updates |

---

## Network Performance

### Bandwidth by Tunnel Type

| Tunnel Type | Typical Bandwidth | Latency Overhead |
|-------------|-------------------|------------------|
| Site-to-Site VPN | 1.25 Gbps/tunnel | 5-15ms |
| Direct Connect 1G | 1 Gbps | 1-3ms |
| Direct Connect 10G | 10 Gbps | 1-2ms |
| GRE (unencrypted) | Limited by Internet | 2-5ms |
| WireGuard | 1-10 Gbps (CPU bound) | 2-5ms |

### Instance Network Performance

See [AWS Network Performance Documentation](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-instance-network-bandwidth.html)

**For HPC/MPI**:
- Use placement groups (cluster strategy)
- Use ENA-enabled instances (c5, c6i, etc.)
- Consider c5n.18xlarge (100 Gbps)

---

## Troubleshooting

### VPN Tunnel Down

```bash
# Check AWS side
aws ec2 describe-vpn-connections \
  --vpn-connection-ids $VPN_ID \
  --query 'VpnConnections[0].VgwTelemetry'

# Check on-prem side (strongSwan)
sudo strongswan status
sudo strongswan statusall

# Check logs
sudo journalctl -u strongswan -n 50
```

### Routing Issues

```bash
# On-prem: Can you reach AWS?
ping 10.1.1.50
traceroute 10.1.1.50

# AWS: Can you reach on-prem?
ping 10.0.1.100
traceroute 10.0.1.100

# Check routing tables
ip route show
netstat -rn
```

### NFS Mount Failures

```bash
# Test NFS connectivity
nc -zv 10.0.1.100 2049

# Test showmount
showmount -e 10.0.1.100

# Check exports on headnode
sudo exportfs -v

# Manual mount test
sudo mount -v -t nfs 10.0.1.100:/nfs /mnt
```

---

## Best Practices

1. **Use Direct Connect** for production workloads with heavy I/O
2. **Use Site-to-Site VPN** for testing or light bursting
3. **Always enable redundancy**: Use 2 VPN tunnels or DX + VPN backup
4. **Monitor tunnel health**: Set up CloudWatch alarms for VPN status
5. **Test failover**: Ensure backup tunnel works if primary fails
6. **Optimize NFS**: Use large rsize/wsize, NFSv4.1, tune for WAN
7. **Use private subnets** for compute nodes (security)
8. **Enable VPC Flow Logs** for troubleshooting
9. **Document your network**: Keep diagram and IP allocations updated
10. **Plan IP space carefully**: Ensure no overlaps between on-prem and AWS

---

## Next Steps

- **On-prem bursting setup**: See [On-Premises to AWS Guide](onprem-to-aws-bursting.md)
- **Security hardening**: See [Security Best Practices](security.md)
- **Monitoring**: See [Monitoring Guide](monitoring.md)
- **Performance tuning**: See [Performance Tuning](performance-tuning.md)
