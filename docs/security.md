# Security Best Practices

This document outlines security considerations and best practices for deploying the AWS Plugin for Slurm.

## Overview

Security for this plugin involves multiple layers:
- AWS IAM permissions
- Network security
- Authentication (Munge)
- Instance metadata security
- Secrets management
- Audit logging

## IAM Security

### Principle of Least Privilege

#### Headnode IAM Policy

**Minimum required permissions:**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EC2FleetManagement",
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
      "Sid": "EC2FleetServiceLinkedRole",
      "Effect": "Allow",
      "Action": "iam:CreateServiceLinkedRole",
      "Resource": "arn:aws:iam::*:role/aws-service-role/ec2fleet.amazonaws.com/AWSServiceRoleForEC2Fleet",
      "Condition": {
        "StringEquals": {
          "iam:AWSServiceName": "ec2fleet.amazonaws.com"
        }
      }
    },
    {
      "Sid": "PassRoleToComputeNodes",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": "arn:aws:iam::ACCOUNT_ID:role/SlurmComputeNodeRole",
      "Condition": {
        "StringEquals": {
          "iam:PassedToService": "ec2.amazonaws.com"
        }
      }
    }
  ]
}
```

**Recommended additions for production:**

```json
{
  "Sid": "RestrictToSpecificSubnets",
  "Effect": "Allow",
  "Action": "ec2:RunInstances",
  "Resource": "arn:aws:ec2:*:*:subnet/*",
  "Condition": {
    "StringEquals": {
      "ec2:Subnet": [
        "arn:aws:ec2:us-east-1:ACCOUNT_ID:subnet/subnet-xxxxx",
        "arn:aws:ec2:us-east-1:ACCOUNT_ID:subnet/subnet-yyyyy"
      ]
    }
  }
}
```

```json
{
  "Sid": "RestrictInstanceTypes",
  "Effect": "Allow",
  "Action": "ec2:RunInstances",
  "Resource": "arn:aws:ec2:*:*:instance/*",
  "Condition": {
    "StringLike": {
      "ec2:InstanceType": [
        "c5.*",
        "c6i.*",
        "m5.*"
      ]
    }
  }
}
```

```json
{
  "Sid": "RequireIMDSv2",
  "Effect": "Deny",
  "Action": "ec2:RunInstances",
  "Resource": "arn:aws:ec2:*:*:instance/*",
  "Condition": {
    "StringNotEquals": {
      "ec2:MetadataHttpTokens": "required"
    }
  }
}
```

#### Compute Node IAM Policy

**Minimum required:**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadInstanceTags",
      "Effect": "Allow",
      "Action": "ec2:DescribeTags",
      "Resource": "*"
    }
  ]
}
```

**Important**: Compute nodes do NOT need permissions to launch or terminate instances.

### IAM Role Trust Policies

**Headnode trust policy:**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "ec2.amazonaws.com"
      },
      "Action": "sts:AssumeRole",
      "Condition": {
        "StringEquals": {
          "aws:SourceAccount": "ACCOUNT_ID"
        },
        "ArnLike": {
          "aws:SourceArn": "arn:aws:ec2:REGION:ACCOUNT_ID:instance/*"
        }
      }
    }
  ]
}
```

### IAM Boundary Policies

For multi-tenant environments, use permission boundaries:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:*",
        "iam:PassRole"
      ],
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "aws:RequestedRegion": "us-east-1"
        }
      }
    },
    {
      "Effect": "Deny",
      "Action": [
        "ec2:*ReservedInstances*",
        "ec2:*Spot*",
        "ec2:*CapacityReservation*"
      ],
      "Resource": "*"
    }
  ]
}
```

## Network Security

### Security Group Configuration

#### Headnode Security Group

**Inbound Rules:**

| Type | Protocol | Port Range | Source | Description |
|------|----------|------------|--------|-------------|
| SSH | TCP | 22 | Your IP/CIDR | Management access |
| Slurm | TCP | 6817-6819 | Compute SG | Slurm communication |
| NFS | TCP | 2049 | Compute SG | NFS file sharing |
| Munge | TCP | 6566 | Compute SG | Munge auth |
| All Traffic | All | All | Headnode SG | Self-reference |

**Outbound Rules:**

| Type | Protocol | Port Range | Destination | Description |
|------|----------|------------|-------------|-------------|
| HTTPS | TCP | 443 | 0.0.0.0/0 | AWS API calls |
| All Traffic | All | All | Compute SG | Node communication |

#### Compute Node Security Group

**Inbound Rules:**

| Type | Protocol | Port Range | Source | Description |
|------|----------|------------|--------|-------------|
| Slurm | TCP | 6818 | Headnode SG | slurmd port |
| All Traffic | All | All | Compute SG | Inter-node (MPI) |

**Outbound Rules:**

| Type | Protocol | Port Range | Destination | Description |
|------|----------|------------|-------------|-------------|
| HTTPS | TCP | 443 | 0.0.0.0/0 | AWS API, packages |
| NFS | TCP | 2049 | Headnode SG | Mount Slurm files |
| All Traffic | All | All | Compute SG | Inter-node (MPI) |

### Network Segmentation

**Recommended VPC Architecture:**

```
┌─────────────────────────────────────────────┐
│                    VPC                       │
│              10.0.0.0/16                     │
│                                              │
│  ┌───────────────────────────────────────┐  │
│  │  Public Subnet - 10.0.1.0/24          │  │
│  │  - Headnode (with Elastic IP)         │  │
│  │  - NAT Gateway                         │  │
│  └───────────────────────────────────────┘  │
│                                              │
│  ┌───────────────────────────────────────┐  │
│  │  Private Subnet 1 - 10.0.10.0/24      │  │
│  │  - Compute Nodes (AZ 1)               │  │
│  └───────────────────────────────────────┘  │
│                                              │
│  ┌───────────────────────────────────────┐  │
│  │  Private Subnet 2 - 10.0.20.0/24      │  │
│  │  - Compute Nodes (AZ 2)               │  │
│  └───────────────────────────────────────┘  │
│                                              │
└─────────────────────────────────────────────┘
```

**Benefits:**
- Headnode accessible from internet (SSH)
- Compute nodes not directly accessible
- Compute nodes access internet via NAT Gateway
- Reduced attack surface

### Network ACLs

Add network ACLs for defense in depth:

**Private Subnet NACL (Compute Nodes):**

**Inbound:**
- Allow TCP 6818 from VPC CIDR (Slurm)
- Allow TCP 2049 from VPC CIDR (NFS)
- Allow ephemeral ports from 0.0.0.0/0 (return traffic)
- Deny all other

**Outbound:**
- Allow HTTPS to 0.0.0.0/0 (AWS APIs)
- Allow NFS to VPC CIDR
- Allow Slurm to VPC CIDR
- Deny all other

### VPC Endpoints

Use VPC endpoints to avoid internet routing for AWS APIs:

```bash
# Create VPC endpoint for EC2
aws ec2 create-vpc-endpoint \
  --vpc-id vpc-xxxxx \
  --service-name com.amazonaws.us-east-1.ec2 \
  --route-table-ids rtb-xxxxx

# Create VPC endpoint for SSM (Session Manager)
aws ec2 create-vpc-endpoint \
  --vpc-id vpc-xxxxx \
  --vpc-endpoint-type Interface \
  --service-name com.amazonaws.us-east-1.ssm \
  --subnet-ids subnet-xxxxx \
  --security-group-ids sg-xxxxx
```

**Benefits:**
- Traffic stays within AWS network
- No NAT Gateway costs for AWS API calls
- Improved security posture

## Munge Authentication

### Key Management

**CRITICAL**: Never use the default Munge key from the CloudFormation template in production.

#### Generating Secure Munge Keys

```bash
# Generate a random 1024-byte key
dd if=/dev/urandom bs=1 count=1024 > munge.key

# Set correct permissions
chmod 600 munge.key
chown munge:munge munge.key
```

#### Storing Munge Keys Securely

**Option 1: AWS Secrets Manager (Recommended)**

```bash
# Store the key
aws secretsmanager create-secret \
  --name slurm/munge-key \
  --description "Munge authentication key for Slurm cluster" \
  --secret-binary fileb://munge.key

# Retrieve in user data
TOKEN=$(curl -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" -s)

REGION=$(curl -H "X-aws-ec2-metadata-token: $TOKEN" \
  -s http://169.254.169.254/latest/meta-data/placement/region)

aws secretsmanager get-secret-value \
  --secret-id slurm/munge-key \
  --query SecretBinary \
  --output text \
  --region $REGION | base64 -d > /etc/munge/munge.key

chmod 600 /etc/munge/munge.key
chown munge:munge /etc/munge/munge.key
```

**Required IAM Permission:**
```json
{
  "Effect": "Allow",
  "Action": [
    "secretsmanager:GetSecretValue"
  ],
  "Resource": "arn:aws:secretsmanager:REGION:ACCOUNT_ID:secret:slurm/munge-key-*"
}
```

**Option 2: AWS Systems Manager Parameter Store**

```bash
# Store the key
aws ssm put-parameter \
  --name /slurm/munge-key \
  --value file://munge.key \
  --type SecureString \
  --key-id alias/aws/ssm

# Retrieve in user data
aws ssm get-parameter \
  --name /slurm/munge-key \
  --with-decryption \
  --query Parameter.Value \
  --output text > /etc/munge/munge.key
```

### Munge Security Best Practices

1. **Rotate keys periodically** (every 90 days)
2. **Unique keys per cluster** - Never reuse keys
3. **Restrict key file permissions** - 600, owned by munge user
4. **Monitor failed authentications** - Check munge logs
5. **Time synchronization** - Use NTP/Chrony (Munge is time-sensitive)

### Testing Munge

```bash
# Test munge on compute node
munge -n | ssh headnode unmunge

# Should output: STATUS: Success
```

## Instance Metadata Security (IMDSv2)

### Enforcing IMDSv2

**In Launch Template:**

```json
{
  "MetadataOptions": {
    "HttpTokens": "required",
    "HttpPutResponseHopLimit": 1,
    "InstanceMetadataTags": "enabled"
  }
}
```

**Via IAM Policy (enforce at account level):**

```json
{
  "Sid": "RequireIMDSv2",
  "Effect": "Deny",
  "Action": "ec2:RunInstances",
  "Resource": "arn:aws:ec2:*:*:instance/*",
  "Condition": {
    "StringNotEquals": {
      "ec2:MetadataHttpTokens": "required"
    }
  }
}
```

### Secure Metadata Access Script

The `get_nodename` script in the CloudFormation template uses IMDSv2:

```bash
#!/bin/bash

# Get IMDSv2 token (required)
TOKEN=$(curl -X PUT "http://169.254.169.254/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" \
    -s --max-time 2)

if [ -z "$TOKEN" ]; then
    echo "Error: Failed to retrieve IMDSv2 token"
    exit 1
fi

# Get Name tag using token
NAME_TAG=$(curl -H "X-aws-ec2-metadata-token: $TOKEN" \
    -s --max-time 2 \
    "http://169.254.169.254/latest/meta-data/tags/instance/Name")

if [ -z "$NAME_TAG" ]; then
    echo "No Name tag found"
    exit 1
else
    echo "$NAME_TAG"
fi
```

### Why IMDSv2?

- **Prevents SSRF attacks** - Requires PUT request with token
- **Hop limit** - Prevents container/pod from accessing host metadata
- **Session-based** - Tokens expire after TTL
- **Defense in depth** - Additional layer beyond security groups

## Secrets Management

### Plugin Configuration Secrets

**DO NOT** store sensitive data in `config.json` or `partitions.json`:

❌ **Bad:**
```json
{
  "AWS_ACCESS_KEY": "AKIAIOSFODNN7EXAMPLE",
  "AWS_SECRET_KEY": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
}
```

✅ **Good:** Use IAM roles (no credentials needed)

### SSH Key Management

**For development:**
- Use EC2 Key Pairs

**For production:**
- Use AWS Systems Manager Session Manager (no SSH keys needed)
- If SSH required, use temporary keys via AWS Certificate Authority

**Enable Session Manager:**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ssm:StartSession"
      ],
      "Resource": [
        "arn:aws:ec2:*:*:instance/*"
      ],
      "Condition": {
        "StringEquals": {
          "ssm:resourceTag/SSMAccess": "true"
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": [
        "ssm:TerminateSession"
      ],
      "Resource": "arn:aws:ssm:*:*:session/${aws:username}-*"
    }
  ]
}
```

## Encryption

### Data at Rest

**EBS Volumes:**

Enable encryption in launch template:

```json
{
  "BlockDeviceMappings": [
    {
      "DeviceName": "/dev/xvda",
      "Ebs": {
        "Encrypted": true,
        "KmsKeyId": "arn:aws:kms:us-east-1:ACCOUNT_ID:key/KEY_ID",
        "VolumeType": "gp3"
      }
    }
  ]
}
```

**NFS Encryption:**

Encrypt NFS traffic with Stunnel or use EFS with encryption:

```bash
# Alternative: Use Amazon EFS instead of NFS
# - Encryption in transit (TLS)
# - Encryption at rest (KMS)
# - Multi-AZ by default
```

### Data in Transit

**Slurm Communication:**

Slurm 20.11+ supports TLS encryption. Enable in `slurm.conf`:

```
# Enable TLS for all Slurm communication
CommunicationParameters=EnableIPv4
SchedulerType=sched/backfill
SlurmctldParameters=enable_configless
CommunicationParameters=use_tls

# TLS certificate paths
SlurmdAuthType=auth/x509
SlurmctldAuthType=auth/x509
```

## Audit Logging

### Enable CloudTrail

```bash
aws cloudtrail create-trail \
  --name slurm-cluster-trail \
  --s3-bucket-name my-cloudtrail-bucket \
  --is-multi-region-trail \
  --enable-log-file-validation

aws cloudtrail start-logging --name slurm-cluster-trail
```

**Monitor these events:**
- `RunInstances` - Track instance launches
- `TerminateInstances` - Track terminations
- `CreateFleet` - Track fleet requests
- `AssumeRole` - Track IAM role usage

### VPC Flow Logs

```bash
aws ec2 create-flow-logs \
  --resource-type VPC \
  --resource-ids vpc-xxxxx \
  --traffic-type ALL \
  --log-destination-type cloud-watch-logs \
  --log-group-name /aws/vpc/slurm-cluster
```

### Plugin Logging

Configure appropriate log levels:

**Development:**
```json
{
  "LogLevel": "DEBUG"
}
```

**Production:**
```json
{
  "LogLevel": "WARNING"
}
```

**Ship logs to CloudWatch:**

```bash
# Install CloudWatch agent
yum install amazon-cloudwatch-agent -y

# Configure to ship plugin logs
cat > /opt/aws/amazon-cloudwatch-agent/etc/config.json <<EOF
{
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "/var/log/slurm/aws_plugin.log",
            "log_group_name": "/aws/slurm/plugin",
            "log_stream_name": "{instance_id}"
          }
        ]
      }
    }
  }
}
EOF

# Start agent
systemctl start amazon-cloudwatch-agent
```

## Compliance

### CIS AWS Foundations Benchmark

Relevant controls:
- 2.1.1 - Deny public access to S3 buckets (if using S3 for shared storage)
- 2.2.1 - Encrypt EBS volumes
- 2.3.1 - Ensure RDS instances are encrypted (if using RDS for accounting)
- 3.1 - Enable CloudTrail
- 4.1 - Ensure no security group allows 0.0.0.0/0 ingress on port 22
- 5.1 - Ensure IAM password policy is strong
- 5.2 - Ensure MFA is enabled for root account

### HIPAA Compliance

If processing PHI:
1. **Sign BAA with AWS**
2. **Encrypt all data** (at rest and in transit)
3. **Audit logging** (CloudTrail, VPC Flow Logs)
4. **Access controls** (IAM, security groups)
5. **Automatic log off** (session timeouts)

### PCI DSS

If processing cardholder data:
1. **Network segmentation** (separate VPC/subnets)
2. **Encryption** (TLS for Slurm, encrypted EBS)
3. **Access control** (IAM, least privilege)
4. **Logging and monitoring** (CloudTrail, CloudWatch)
5. **Vulnerability scanning** (AWS Inspector)

## Security Checklist

### Pre-Deployment

- [ ] Generate unique Munge key
- [ ] Store Munge key in Secrets Manager
- [ ] Review IAM policies (least privilege)
- [ ] Configure security groups (restrict SSH)
- [ ] Enable IMDSv2 enforcement
- [ ] Enable EBS encryption
- [ ] Create VPC endpoints (optional)

### Post-Deployment

- [ ] Enable CloudTrail
- [ ] Enable VPC Flow Logs
- [ ] Configure CloudWatch alarms
- [ ] Test Munge authentication
- [ ] Verify IMDSv2 (check metadata access)
- [ ] Review initial CloudTrail events
- [ ] Document emergency contacts

### Ongoing

- [ ] Rotate Munge keys every 90 days
- [ ] Review CloudTrail logs monthly
- [ ] Update AMIs with security patches
- [ ] Review IAM permissions quarterly
- [ ] Test incident response procedures
- [ ] Review security group rules
- [ ] Audit SSH keys and remove stale keys

## Incident Response

### Compromised Headnode

1. **Isolate** - Change security group to block all traffic
2. **Snapshot** - Take EBS snapshot for forensics
3. **Terminate** compute nodes - Prevent lateral movement
4. **Rotate** Munge key
5. **Review** CloudTrail logs
6. **Rebuild** from known good state

### Compromised Compute Node

1. **Terminate** the node
2. **Review** what job was running
3. **Check** for privilege escalation
4. **Review** CloudTrail for API calls from node
5. **Scan** shared filesystems for malware

### Unauthorized API Calls

1. **Identify** compromised credentials
2. **Rotate** or delete credentials
3. **Review** CloudTrail for scope of access
4. **Terminate** unauthorized resources
5. **Document** incident timeline

## Security Resources

- [AWS Security Best Practices](https://docs.aws.amazon.com/security/)
- [Slurm Security](https://slurm.schedmd.com/security.html)
- [CIS AWS Foundations Benchmark](https://www.cisecurity.org/benchmark/amazon_web_services)
- [NIST Cybersecurity Framework](https://www.nist.gov/cyberframework)
