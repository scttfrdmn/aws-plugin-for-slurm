# Manual Installation Guide

This guide provides step-by-step instructions for manually installing and configuring the AWS Plugin for Slurm.

## Prerequisites

### Slurm Headnode

- You must have a functional Slurm headnode (tested with Slurm 20.02.3, compatible with any version supporting power saving mode)
- The headnode can be located anywhere (on-premises or in AWS)

### AWS Resources

- One or more VPC subnets for launching EC2 compute nodes
- If the headnode is not on AWS, establish private connectivity (e.g., VPN) between the headnode and the subnets
- An EC2 launch template configured with:
  - AMI ID
  - Security group(s)
  - IAM role for compute nodes
  - (Optional) Key pair
  - (Optional) User data scripts

### Compute Node Configuration

**IMPORTANT**: Compute nodes must specify their cluster name when launching `slurmd`. The cluster name is retrieved from the EC2 instance tag.

#### Configure slurmd to Use Instance Tag as Node Name

Create a script that returns the node name from the EC2 tag using IMDSv2 (Instance Metadata Service v2):

```bash
cat > /fullpath/get_nodename <<'EOF'
#!/bin/bash

# Function to get IMDSv2 token
get_imds_token() {
    TOKEN=$(curl -X PUT "http://169.254.169.254/latest/api/token" \
        -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" \
        -s --max-time 2)
    echo "$TOKEN"
}

# Function to get metadata using IMDSv2
get_metadata() {
    local path=$1
    local token=$2
    curl -H "X-aws-ec2-metadata-token: $token" \
        -s --max-time 2 \
        "http://169.254.169.254/latest/meta-data/${path}"
}

# Get IMDSv2 token
TOKEN=$(get_imds_token)

if [ -z "$TOKEN" ]; then
    echo "Error: Failed to retrieve IMDSv2 token"
    echo "$(hostname)"
    exit 0
fi

# Get the Name tag directly from instance metadata
NAME_TAG=$(get_metadata "tags/instance/Name" "$TOKEN")

# Check if the Name tag exists
if [ -z "$NAME_TAG" ] || [ "$NAME_TAG" = "None" ]; then
    echo "$(hostname)"
else
    echo "$NAME_TAG"
fi
EOF
chmod +x /fullpath/get_nodename
```

**Important Notes:**
- This script uses **IMDSv2** (Instance Metadata Service version 2) for enhanced security
- You must enable instance metadata tags by setting `InstanceMetadataTags` to `enabled` in your launch template
- IMDSv2 should be enforced in your launch template (`HttpTokens: required`)

**Security Advantage of IMDSv2:**
IMDSv2 provides protection against SSRF (Server-Side Request Forgery) attacks by requiring a PUT request to obtain a token before accessing metadata. This is a security best practice for all EC2 instances.

**Alternative IMDSv1 Script (Not Recommended):**

If you must use IMDSv1 (not recommended for security reasons):

```bash
cat > /fullpath/get_nodename <<'EOF'
#!/bin/bash
instanceid=$(curl --fail -m 2 -s http://169.254.169.254/latest/meta-data/instance-id)
if [[ ! -z "$instanceid" ]]; then
   hostname=$(curl -s http://169.254.169.254/latest/meta-data/tags/instance/Name)
fi
if [ ! -z "$hostname" ] && [ "$hostname" != "None" ]; then
   echo $hostname
else
   echo $(hostname)
fi
EOF
chmod +x /fullpath/get_nodename
```

Modify the systemd service file `/lib/systemd/system/slurmd.service`:

```ini
ExecStartPre=/bin/bash -c "/bin/systemctl set-environment SLURM_NODENAME=$(/fullpath/get_nodename)"
ExecStart=/nfs/slurm/sbin/slurmd -N $SLURM_NODENAME $SLURMD_OPTIONS
```

## Installation Steps

### 1. Install Dependencies

Install Python 3, boto3, AWS CLI, and Munge on the headnode:

```bash
sudo yum install python3 python3-pip munge munge-libs munge-devel -y
sudo pip3 install boto3
sudo pip3 install awscli
```

###  2. Configure Munge Authentication

Munge provides authentication between Slurm nodes. **All nodes must share the same Munge key.**

#### Generate Secure Munge Key

**IMPORTANT**: Never use default or hard-coded Munge keys in production.

```bash
# Generate a random 1024-byte key
sudo dd if=/dev/urandom bs=1 count=1024 > /tmp/munge.key

# Move to correct location
sudo mv /tmp/munge.key /etc/munge/munge.key

# Set correct permissions (CRITICAL)
sudo chown munge:munge /etc/munge/munge.key
sudo chmod 600 /etc/munge/munge.key

# Set directory permissions
sudo chown -R munge:munge /etc/munge/ /var/log/munge/ /var/lib/munge/
sudo chmod 0700 /etc/munge/ /var/log/munge/ /var/lib/munge/
```

####  Start Munge Service

```bash
# Enable and start munge
sudo systemctl enable munge
sudo systemctl start munge

# Verify munge is running
sudo systemctl status munge

# Test munge
munge -n | unmunge
# Should output: STATUS: Success
```

#### Distribute Munge Key to Compute Nodes

**Option 1: Include in AMI (Recommended)**

Pre-bake the munge key into your compute node AMI. This is the most secure and efficient method.

**Option 2: User Data Script**

Include in your launch template user data (less secure, not recommended for production):

```bash
#!/bin/bash
# Install munge
yum install -y munge munge-libs

# Copy munge key from shared storage or fetch from secrets manager
# Example using NFS (assumes NFS mount exists):
cp /nfs/etc/munge/munge.key /etc/munge/munge.key

# Or fetch from AWS Secrets Manager (more secure):
aws secretsmanager get-secret-value \
  --secret-id slurm/munge-key \
  --query SecretBinary \
  --output text | base64 -d > /etc/munge/munge.key

# Set permissions
chown munge:munge /etc/munge/munge.key
chmod 600 /etc/munge/munge.key
chown -R munge:munge /etc/munge/ /var/log/munge/
chmod 0700 /etc/munge/ /var/log/munge/

# Start munge
systemctl enable munge
systemctl start munge
```

**Option 3: AWS Secrets Manager (Most Secure for Production)**

```bash
# On headnode, store the key
sudo aws secretsmanager create-secret \
  --name slurm/munge-key \
  --description "Munge authentication key for Slurm cluster" \
  --secret-binary fileb:///etc/munge/munge.key

# Grant compute nodes permission to read secret
# Add to compute node IAM role:
# {
#   "Effect": "Allow",
#   "Action": ["secretsmanager:GetSecretValue"],
#   "Resource": "arn:aws:secretsmanager:REGION:ACCOUNT:secret:slurm/munge-key-*"
# }

# On compute nodes (in user data):
aws secretsmanager get-secret-value \
  --secret-id slurm/munge-key \
  --query SecretBinary \
  --output text | base64 -d > /etc/munge/munge.key

chown munge:munge /etc/munge/munge.key
chmod 600 /etc/munge/munge.key
```

#### Munge Security Best Practices

1. **Never commit Munge keys to source control**
2. **Use unique keys per cluster** - Don't reuse keys
3. **Rotate keys periodically** (every 90 days recommended)
4. **Verify file permissions** - Must be 600, owned by munge user
5. **Time synchronization is critical** - Use NTP/Chrony on all nodes
   ```bash
   # Install and configure chrony
   sudo yum install -y chrony
   sudo systemctl enable chronyd
   sudo systemctl start chronyd

   # Verify time sync
   chronyc tracking
   ```
6. **Test Munge before deploying** - Ensure it works between headnode and test compute node

#### Troubleshooting Munge

**Test Munge encryption/decryption:**

```bash
# On headnode
munge -n | unmunge
# Should output: STATUS: Success (uid=XXX gid=XXX)

# Test from headnode to compute node
munge -n | ssh compute-node unmunge
# Should also output: STATUS: Success
```

**Common Munge errors:**

- **"Credential replayed"** - Check time synchronization
- **"Credential expired"** - Check time synchronization
- **"Invalid credential"** - Keys don't match between nodes
- **Permission denied** - Check munge.key permissions (must be 600)

### 3. Download Plugin Files

Copy the Python files to a folder (e.g., `$SLURM_ROOT/etc/aws`) and make them executable:

```bash
cd /fullpath
wget -q https://github.com/scttfrdmn/aws-plugin-for-slurm/raw/plugin-v3/common.py
wget -q https://github.com/scttfrdmn/aws-plugin-for-slurm/raw/plugin-v3/resume.py
wget -q https://github.com/scttfrdmn/aws-plugin-for-slurm/raw/plugin-v3/suspend.py
wget -q https://github.com/scttfrdmn/aws-plugin-for-slurm/raw/plugin-v3/generate_conf.py
wget -q https://github.com/scttfrdmn/aws-plugin-for-slurm/raw/plugin-v3/change_state.py
chmod +x *.py
```

### 4. Configure AWS Permissions

Grant the headnode AWS permissions to make EC2 requests.

#### If Headnode is on AWS

**Create IAM role and instance profile:**

```bash
# 1. Create trust policy document
cat > /tmp/trust-policy.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "ec2.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}
EOF

# 2. Create the IAM role
aws iam create-role \
  --role-name SlurmHeadnodeRole \
  --assume-role-policy-document file:///tmp/trust-policy.json \
  --description "Role for Slurm headnode to manage EC2 compute nodes"

# 3. Create permissions policy document
cat > /tmp/headnode-policy.json <<'EOF'
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
      "Resource": "arn:aws:iam::ACCOUNT_ID:role/EC2ComputeNodeRole"
    }
  ]
}
EOF

# Replace ACCOUNT_ID with your AWS account ID
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
sed -i "s/ACCOUNT_ID/$ACCOUNT_ID/g" /tmp/headnode-policy.json

# 4. Attach inline policy to role
aws iam put-role-policy \
  --role-name SlurmHeadnodeRole \
  --policy-name SlurmHeadnodePolicy \
  --policy-document file:///tmp/headnode-policy.json

# 5. Create instance profile
aws iam create-instance-profile \
  --instance-profile-name SlurmHeadnodeProfile

# 6. Add role to instance profile
aws iam add-role-to-instance-profile \
  --instance-profile-name SlurmHeadnodeProfile \
  --role-name SlurmHeadnodeRole

# 7. Attach the instance profile to your headnode
HEADNODE_INSTANCE_ID="i-xxxxxxxxxxxxx"  # Replace with your instance ID
aws ec2 associate-iam-instance-profile \
  --instance-id $HEADNODE_INSTANCE_ID \
  --iam-instance-profile Name=SlurmHeadnodeProfile
```

**Note:** Replace `ACCOUNT_ID` and `HEADNODE_INSTANCE_ID` with your actual values.

#### If Headnode is Not on AWS

**Create IAM user with programmatic access:**

```bash
# 1. Create IAM user
aws iam create-user \
  --user-name slurm-headnode-user \
  --tags Key=Purpose,Value=SlurmHeadnode

# 2. Create and attach inline policy
# (Use same policy document from above)
cat > /tmp/headnode-policy.json <<'EOF'
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
      "Resource": "arn:aws:iam::ACCOUNT_ID:role/EC2ComputeNodeRole"
    }
  ]
}
EOF

# Replace ACCOUNT_ID
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
sed -i "s/ACCOUNT_ID/$ACCOUNT_ID/g" /tmp/headnode-policy.json

aws iam put-user-policy \
  --user-name slurm-headnode-user \
  --policy-name SlurmHeadnodePolicy \
  --policy-document file:///tmp/headnode-policy.json

# 3. Create access key
aws iam create-access-key \
  --user-name slurm-headnode-user \
  --output json > /tmp/access-key.json

# Extract credentials
ACCESS_KEY_ID=$(jq -r '.AccessKey.AccessKeyId' /tmp/access-key.json)
SECRET_ACCESS_KEY=$(jq -r '.AccessKey.SecretAccessKey' /tmp/access-key.json)

echo "Access Key ID: $ACCESS_KEY_ID"
echo "Secret Access Key: $SECRET_ACCESS_KEY"

# IMPORTANT: Save these credentials securely, then delete the file
# rm /tmp/access-key.json

# 4. Configure AWS CLI on headnode
# Default profile
aws configure set aws_access_key_id $ACCESS_KEY_ID
aws configure set aws_secret_access_key $SECRET_ACCESS_KEY
aws configure set region us-east-1  # Set your region

# Or create a named profile (reference in partitions.json ProfileName)
aws configure set aws_access_key_id $ACCESS_KEY_ID --profile slurm
aws configure set aws_secret_access_key $SECRET_ACCESS_KEY --profile slurm
aws configure set region us-east-1 --profile slurm
```

**Security best practice:** Store credentials in AWS Secrets Manager instead of local files.

#### Minimum Required Permissions

```
ec2:CreateFleet
ec2:RunInstances
ec2:TerminateInstances
ec2:CreateTags
ec2:DescribeInstances
iam:CreateServiceLinkedRole (required if you never used EC2 Fleet in your account)
iam:PassRole (restrict to ARN of the EC2 role for compute nodes)
```

### 5. Create IAM Role for Compute Nodes

**Create IAM role for compute nodes:**

```bash
# 1. Create trust policy (same as headnode)
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

# 2. Create the IAM role
aws iam create-role \
  --role-name EC2ComputeNodeRole \
  --assume-role-policy-document file:///tmp/compute-trust-policy.json \
  --description "Role for Slurm compute nodes"

# 3. Create minimal permissions policy
cat > /tmp/compute-policy.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "ec2:DescribeTags",
    "Resource": "*"
  }]
}
EOF

# 4. Attach inline policy
aws iam put-role-policy \
  --role-name EC2ComputeNodeRole \
  --policy-name ComputeNodePolicy \
  --policy-document file:///tmp/compute-policy.json

# 5. Create instance profile
aws iam create-instance-profile \
  --instance-profile-name EC2ComputeNodeProfile

# 6. Add role to instance profile
aws iam add-role-to-instance-profile \
  --instance-profile-name EC2ComputeNodeProfile \
  --role-name EC2ComputeNodeRole

# 7. Get the instance profile ARN for use in launch template
aws iam get-instance-profile \
  --instance-profile-name EC2ComputeNodeProfile \
  --query 'InstanceProfile.Arn' \
  --output text
```

### 6. Create EC2 Launch Template(s)

**Create launch template for compute nodes:**

```bash
# 1. Get latest Amazon Linux 2 AMI ID
AMI_ID=$(aws ec2 describe-images \
  --owners amazon \
  --filters "Name=name,Values=amzn2-ami-hvm-*-x86_64-gp2" \
            "Name=state,Values=available" \
  --query 'Images | sort_by(@, &CreationDate) | [-1].ImageId' \
  --output text)

echo "Using AMI: $AMI_ID"

# 2. Get security group ID (create one if needed)
# Example: Create a basic security group
VPC_ID="vpc-xxxxxxxxxxxxx"  # Replace with your VPC ID

SG_ID=$(aws ec2 create-security-group \
  --group-name slurm-compute-nodes \
  --description "Security group for Slurm compute nodes" \
  --vpc-id $VPC_ID \
  --query 'GroupId' \
  --output text)

# Allow SSH from headnode (optional)
HEADNODE_SG="sg-xxxxxxxxxxxxx"  # Replace with headnode security group
aws ec2 authorize-security-group-ingress \
  --group-id $SG_ID \
  --source-group $HEADNODE_SG \
  --protocol all

echo "Security Group: $SG_ID"

# 3. Get the instance profile ARN from step 5
INSTANCE_PROFILE_ARN=$(aws iam get-instance-profile \
  --instance-profile-name EC2ComputeNodeProfile \
  --query 'InstanceProfile.Arn' \
  --output text)

# 4. Create launch template
aws ec2 create-launch-template \
  --launch-template-name slurm-compute-template \
  --version-description "Initial version for Slurm compute nodes" \
  --launch-template-data "{
    \"ImageId\": \"$AMI_ID\",
    \"IamInstanceProfile\": {
      \"Arn\": \"$INSTANCE_PROFILE_ARN\"
    },
    \"SecurityGroupIds\": [\"$SG_ID\"],
    \"MetadataOptions\": {
      \"HttpTokens\": \"required\",
      \"HttpPutResponseHopLimit\": 1,
      \"InstanceMetadataTags\": \"enabled\"
    },
    \"TagSpecifications\": [{
      \"ResourceType\": \"instance\",
      \"Tags\": [{
        \"Key\": \"ManagedBy\",
        \"Value\": \"Slurm\"
      }]
    }]
  }"

# 5. Get launch template ID for configuration
TEMPLATE_ID=$(aws ec2 describe-launch-templates \
  --launch-template-names slurm-compute-template \
  --query 'LaunchTemplates[0].LaunchTemplateId' \
  --output text)

echo "Launch Template ID: $TEMPLATE_ID"
echo "Use this ID in partitions.json LaunchTemplateId field"
```

**Optional additions:**

```bash
# Add SSH key pair (if needed)
aws ec2 create-launch-template-version \
  --launch-template-id $TEMPLATE_ID \
  --source-version 1 \
  --launch-template-data '{"KeyName":"my-key-pair"}'

# Add user data script (example: mount NFS)
cat > /tmp/user-data.sh <<'EOF'
#!/bin/bash
# Mount NFS from headnode
mkdir -p /nfs
mount -t nfs headnode-ip:/nfs /nfs
EOF

aws ec2 create-launch-template-version \
  --launch-template-id $TEMPLATE_ID \
  --source-version 1 \
  --launch-template-data "{\"UserData\":\"$(base64 -w0 /tmp/user-data.sh)\"}"
```

**Note:** You'll need multiple templates if your compute nodes require different configurations (e.g., GPU vs CPU nodes).

### 7. Create Configuration Files

Create `config.json` and `partitions.json` in the same folder as the Python files.

See the [Configuration Reference](configuration.md) for detailed schema and parameters.

### 8. Generate Slurm Configuration

Run `generate_conf.py` to create Slurm configuration:

```bash
cd /fullpath
./generate_conf.py
```

This creates `slurm.conf.aws` with output similar to:

```
PrivateData=CLOUD
ResumeProgram=/slurm/etc/aws/resume.py
SuspendRate=100
# ...More Slurm parameters

NodeName=aws-node-[0-99] State=CLOUD CPUs=4
Partition=aws Nodes=aws-node-[0-99] Default=No MaxTime=INFINITE State=UP
```

Append the contents of `slurm.conf.aws` to your main `slurm.conf` file, then refresh the Slurm configuration:

```bash
scontrol reconfigure
# Or restart slurmctld
```

### 9. Configure Cron Job

Set up a cron job to run `change_state.py` every minute. This script manages nodes stuck in transient or undesired states.

```bash
sudo crontab -e
# Or if Slurm user is not root:
# sudo crontab -e -u slurmuser
```

Add this line (adjust the path to your installation):

```
* * * * * /fullpath/change_state.py &>/dev/null
```

## Testing the Installation

### Manual Testing

You can manually test the resume and suspend programs:

```bash
# Test resuming a node
/fullpath/resume.py partition-nodegroup-0

# Test suspending a node
/fullpath/suspend.py partition-nodegroup-0
```

### Submit a Test Job

1. Check available partitions:
   ```bash
   sinfo
   ```

2. Submit a simple test job:
   ```bash
   srun -p aws hostname
   ```

3. Monitor instance launch:
   ```bash
   # Watch for new instances
   watch 'aws ec2 describe-instances \
     --filters "Name=tag:ManagedBy,Values=Slurm" \
               "Name=instance-state-name,Values=pending,running" \
     --query "Reservations[].Instances[].[InstanceId,State.Name,Tags[?Key==\`Name\`].Value|[0]]" \
     --output table'
   ```

4. After job completion, the node will remain idle for `SuspendTime` seconds before being terminated

## Post-Installation

### Monitoring

- Check plugin logs: `tail -f /var/log/slurm/aws.log` (or your configured `LogFileName`)
- Monitor Slurm: `sinfo`, `squeue`, `scontrol show nodes`
- Watch instances via CLI:
  ```bash
  aws ec2 describe-instances \
    --filters "Name=tag:ManagedBy,Values=Slurm" \
    --query "Reservations[].Instances[].[InstanceId,State.Name,LaunchTime]" \
    --output table
  ```

### Common Issues

See the [Troubleshooting Guide](troubleshooting.md) for solutions to common problems.
