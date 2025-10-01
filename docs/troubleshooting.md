# Troubleshooting Guide

Common issues and solutions for the AWS Plugin for Slurm.

## Quick Diagnostics

Before diving into specific issues, run these quick checks:

```bash
# 1. Check plugin logs
tail -50 /var/log/slurm/aws_plugin.log

# 2. Check Slurm node states
sinfo -p cloud

# 3. Check AWS instances
aws ec2 describe-instances \
  --filters "Name=tag:ManagedBy,Values=Slurm" \
            "Name=instance-state-name,Values=pending,running" \
  --query "Reservations[].Instances[].[InstanceId,State.Name,PrivateIpAddress,Tags[?Key=='Name'].Value|[0]]" \
  --output table

# 4. Test AWS credentials
aws sts get-caller-identity

# 5. For on-prem deployments: Check VPN status
aws ec2 describe-vpn-connections --query 'VpnConnections[*].VgwTelemetry' --output table
```

---

## Node State Reference

Understanding Slurm node states in cloud bursting:

| State | Meaning | Expected Duration | What's Happening |
|-------|---------|-------------------|------------------|
| `idle~` | Powered down, available | Steady state | Node is not running (no AWS cost) |
| `alloc#` | Powering up | 30-120 seconds | Plugin launching instance, booting, joining cluster |
| `alloc` | Running job | Job duration | Node running, executing workload |
| `idle` | Running, no job | Up to SuspendTime | Node idle, waiting for work or suspension |
| `down~` | Powered down, unavailable | After cleanup | Node failed, powered down |
| `down#` | Powering down | Brief | Node being suspended |
| `down*` | Failed, needs cleanup | Until change_state.py runs | Node in bad state, needs admin intervention |

---

## AWS Quotas and Service Limits

**Common Blocker**: AWS service quotas can prevent instance launches even when configuration is correct.

### Check Your Quotas

```bash
# Check vCPU limits for On-Demand instances
aws service-quotas get-service-quota \
  --service-code ec2 \
  --quota-code L-1216C47A \
  --region us-east-1 \
  --query 'Quota.Value'

# Check vCPU limits for Spot instances
aws service-quotas get-service-quota \
  --service-code ec2 \
  --quota-code L-34B43A08 \
  --region us-east-1 \
  --query 'Quota.Value'

# List all EC2 quotas
aws service-quotas list-service-quotas \
  --service-code ec2 \
  --region us-east-1 \
  --query 'Quotas[?contains(QuotaName, `vCPU`)].{Name:QuotaName,Value:Value}' \
  --output table
```

### Common Quota Issues

#### Issue: "InsufficientInstanceCapacity" or "VcpuLimitExceeded"

**Symptom**: CreateFleet fails, nodes stuck in `alloc#`, no instances launch

**Diagnostic**:
```bash
# Check CreateFleet errors in CloudTrail
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=CreateFleet \
  --max-results 5 \
  --query 'Events[*].CloudTrailEvent' \
  --output text | jq -r '. | fromjson | select(.errorCode != null)'
```

**Solutions**:

1. **Request quota increase**:
   ```bash
   # Request increase for On-Demand vCPUs (example: 256 vCPUs)
   aws service-quotas request-service-quota-increase \
     --service-code ec2 \
     --quota-code L-1216C47A \
     --desired-value 256 \
     --region us-east-1

   # Check request status
   aws service-quotas list-requested-service-quota-change-history \
     --service-code ec2 \
     --region us-east-1
   ```

2. **Use instance type flexibility**:
   ```json
   "LaunchTemplateOverrides": [
     {"InstanceType": "c5.xlarge"},
     {"InstanceType": "c5a.xlarge"},
     {"InstanceType": "c6i.xlarge"},
     {"InstanceType": "m5.xlarge"}
   ]
   ```

3. **Spread across multiple AZs**:
   ```json
   "SubnetIds": [
     "subnet-11111111",  // us-east-1a
     "subnet-22222222",  // us-east-1b
     "subnet-33333333"   // us-east-1c
   ]
   ```

4. **Use Spot instances** (separate quota, often higher):
   ```json
   "PurchasingOption": "spot"
   ```

#### Common EC2 Quotas to Check

| Quota Name | Quota Code | Default | Notes |
|------------|------------|---------|-------|
| Running On-Demand Standard instances | L-1216C47A | 5-1,152 vCPUs | Varies by account age/region |
| Running On-Demand F instances | L-74FC7D96 | 128 vCPUs | GPU instances (P3, G4, etc.) |
| Running On-Demand G instances | L-DB2E81BA | 128 vCPUs | Graphics instances |
| Running On-Demand P instances | L-417A185B | 128 vCPUs | High-end GPU (P4, P5) |
| Running Dedicated Hosts | L-81657583 | 0-2 | Dedicated hardware |
| All Standard Spot Instance Requests | L-34B43A08 | 5-1,152 vCPUs | Usually higher than On-Demand |
| EC2-VPC Elastic IPs | L-0263D0A3 | 5 | If using public IPs |

**Pro tip**: New AWS accounts have lower quotas. Request increases early, as they can take 24-48 hours to process.

---

## Installation and Configuration Issues

### boto3 Import Error

**Symptom**: Plugin scripts fail with `ModuleNotFoundError: No module named 'boto3'`

**Solution**:
```bash
# Install for system Python
sudo pip3 install boto3

# Or install for specific user (if Slurm runs as 'slurm' user)
sudo -u slurm pip3 install --user boto3

# Verify installation
python3 -c "import boto3; print(boto3.__version__)"
```

### AWS CLI Not Found

**Symptom**: Cannot configure AWS credentials or scripts fail with `aws: command not found`

**Solution**:
```bash
sudo pip3 install awscli

# Verify
aws --version
```

### Permission Denied When Executing Scripts

**Symptom**: `bash: ./resume.py: Permission denied`

**Solution**:
```bash
sudo chmod +x /etc/slurm/aws-plugin/*.py

# Verify
ls -la /etc/slurm/aws-plugin/
```

### JSON Configuration Parse Error

**Symptom**: Plugin scripts fail with JSON parse error

**Solution**:
```bash
# Validate JSON syntax
python3 -m json.tool /etc/slurm/aws-plugin/config.json
python3 -m json.tool /etc/slurm/aws-plugin/partitions.json

# Or use jq
jq . /etc/slurm/aws-plugin/config.json
```

**Common JSON issues**:
- Trailing commas: `{"key": "value",}`
- Unquoted strings: `{key: "value"}`
- Missing commas: `{"a": "1" "b": "2"}`
- Comments (JSON doesn't support `//) or /* */`)

### Nodes Not Appearing in sinfo

**Symptom**: After running `generate_conf.py` and reconfiguring Slurm, nodes don't appear

**Solutions**:

1. **Verify configuration was appended**:
   ```bash
   grep "PartitionName=cloud" /etc/slurm/slurm.conf
   grep "NodeName=cloud" /etc/slurm/slurm.conf
   grep "PrivateData=CLOUD" /etc/slurm/slurm.conf
   ```

2. **Reconfigure Slurm**:
   ```bash
   # Test configuration first
   sudo slurmctld -t

   # If valid, reconfigure
   sudo scontrol reconfigure

   # Or restart slurmctld
   sudo systemctl restart slurmctld
   ```

3. **Check slurmctld logs**:
   ```bash
   sudo tail -50 /var/log/slurm/slurmctld.log
   # Look for configuration errors
   ```

---

## AWS Permission Issues

### Access Denied Errors

**Symptom**: Plugin logs show `botocore.exceptions.ClientError: An error occurred (UnauthorizedOperation)`

**Diagnostic**:
```bash
# Test AWS credentials
aws sts get-caller-identity

# Test basic EC2 access
aws ec2 describe-instances --max-results 1 --region YOUR_REGION
```

**Solutions**:

1. **For on-prem headnode with IAM user**:
   ```bash
   # Check credentials are configured
   cat ~/.aws/credentials
   cat ~/.aws/config

   # Or for slurm user
   sudo -u slurm cat ~slurm/.aws/credentials

   # Re-configure if needed
   sudo -u slurm aws configure
   ```

2. **For headnode on AWS EC2**:
   ```bash
   # Check IAM role is attached
   aws ec2 describe-instances \
     --instance-ids $(ec2-metadata --instance-id | cut -d' ' -f2) \
     --query 'Reservations[0].Instances[0].IamInstanceProfile.Arn'
   ```

3. **Verify required permissions** (see [Security Guide](security.md) for full policy):
   - `ec2:CreateFleet`
   - `ec2:RunInstances`
   - `ec2:TerminateInstances`
   - `ec2:CreateTags`
   - `ec2:DescribeInstances`
   - `ec2:DescribeTags`
   - `iam:PassRole`

### PassRole Error

**Symptom**: `User: arn:aws:iam::ACCOUNT:user/NAME is not authorized to perform: iam:PassRole`

**Solution**: Add `iam:PassRole` permission for the compute node IAM role:

```json
{
  "Effect": "Allow",
  "Action": "iam:PassRole",
  "Resource": "arn:aws:iam::ACCOUNT_ID:role/SlurmComputeNodeRole"
}
```

Apply to headnode's IAM user or role.

### Wrong Region

**Symptom**: Plugin can't find instances, CreateFleet fails silently

**Diagnostic**:
```bash
# Check configured region
aws configure get region

# Check region in partitions.json
grep -A2 Region /etc/slurm/aws-plugin/partitions.json
```

**Solution**: Ensure region in `partitions.json` matches AWS CLI configuration.

---

## On-Premises to AWS Specific Issues

### VPN Connectivity Issues

#### Issue: VPN Tunnel Down

**Symptom**: Nodes stuck in `alloc#`, cannot ping AWS instances from headnode

**Diagnostic**:
```bash
# Check VPN status
aws ec2 describe-vpn-connections \
  --query 'VpnConnections[*].[VpnConnectionId,State,VgwTelemetry[*].[OutsideIpAddress,Status,StatusMessage]]' \
  --output table

# Test connectivity to AWS subnet
ping 10.1.1.1  # Replace with your AWS subnet IP
```

**Solutions**:

1. **Check on-prem VPN configuration**:
   ```bash
   # For strongSwan
   sudo strongswan status
   sudo strongswan statusall

   # For pfSense/OPNsense
   # Check Status > IPsec in web UI

   # Check logs
   sudo tail -50 /var/log/messages | grep charon
   ```

2. **Check AWS side routing**:
   ```bash
   # Verify route propagation is enabled
   aws ec2 describe-route-tables \
     --filters "Name=vpc-id,Values=YOUR_VPC_ID" \
     --query 'RouteTables[*].PropagatingVgws'
   ```

3. **Verify firewall rules** allow IPsec traffic:
   - UDP 500 (IKE)
   - UDP 4500 (NAT-T)
   - Protocol 50 (ESP)

4. **Pre-shared key mismatch**:
   - Verify PSK matches in VPN config and AWS VPN connection

#### Issue: Routing Not Working

**Symptom**: VPN is UP but cannot reach AWS instances

**Diagnostic**:
```bash
# Check routes on headnode
ip route show | grep 10.1.0.0

# Traceroute to AWS instance
traceroute 10.1.1.X
```

**Solutions**:

1. **Add static route** (if not using BGP):
   ```bash
   # Temporary
   sudo ip route add 10.1.0.0/16 dev ipsec0

   # Permanent - add to network config or router
   ```

2. **Check AWS route table** has on-prem CIDR:
   ```bash
   aws ec2 describe-route-tables \
     --route-table-ids rtb-xxxxx \
     --query 'RouteTables[0].Routes'
   ```

### NFS Mount Failures

#### Issue: Mount Fails from AWS to On-Prem

**Symptom**: Instance console log shows `mount.nfs: Connection timed out`

**Diagnostic**:
```bash
# On headnode, check NFS exports
sudo exportfs -v

# Check if AWS CIDR is in exports
sudo exportfs -v | grep 10.1

# Test from AWS instance
showmount -e 10.0.1.100  # Headnode IP
nc -zv 10.0.1.100 2049   # Test NFS port
```

**Solutions**:

1. **Add AWS VPC CIDR to exports**:
   ```bash
   # On headnode
   sudo vi /etc/exports
   # Add:
   /nfs 10.1.0.0/16(rw,sync,no_root_squash,no_subtree_check)

   # Re-export
   sudo exportfs -ra

   # Verify
   sudo exportfs -v | grep 10.1
   ```

2. **Allow NFS through firewall**:
   ```bash
   # On headnode
   sudo firewall-cmd --permanent --add-service=nfs
   sudo firewall-cmd --permanent --add-service=mountd
   sudo firewall-cmd --permanent --add-service=rpc-bind
   sudo firewall-cmd --reload

   # Or for iptables
   sudo iptables -A INPUT -s 10.1.0.0/16 -p tcp -m multiport --dports 111,2049 -j ACCEPT
   sudo iptables -A INPUT -s 10.1.0.0/16 -p udp -m multiport --dports 111,2049 -j ACCEPT
   ```

3. **Check AWS security group** allows traffic from on-prem:
   ```bash
   aws ec2 describe-security-groups \
     --group-ids sg-xxxxx \
     --query 'SecurityGroups[0].IpPermissions'
   ```

4. **Test manual mount** on AWS instance:
   ```bash
   # SSH to AWS instance
   sudo mkdir -p /mnt/test
   sudo mount -v -t nfs 10.0.1.100:/nfs /mnt/test
   ls /mnt/test
   ```

### Munge Authentication Failures

#### Issue: Munge Key Mismatch

**Symptom**: slurmd logs show "Munge encode failed: Invalid credential"

**Diagnostic**:
```bash
# Get MD5 hash on headnode
md5sum /etc/munge/munge.key

# Get MD5 hash on AWS instance (SSH in)
md5sum /etc/munge/munge.key

# Hashes MUST match exactly
```

**Solutions**:

1. **Verify Munge key retrieval from Secrets Manager**:
   ```bash
   # On AWS instance, check if key was retrieved
   ls -la /etc/munge/munge.key

   # Check slurm-init.sh logs
   cat /var/log/slurm-init.log
   ```

2. **Verify IAM permissions** for compute nodes:
   ```bash
   # Should have secretsmanager:GetSecretValue
   aws iam get-role-policy \
     --role-name SlurmComputeNodeRole \
     --policy-name ComputeNodePolicy
   ```

3. **Test Munge retrieval manually**:
   ```bash
   # On AWS instance
   aws secretsmanager get-secret-value \
     --secret-id slurm/munge-key \
     --region YOUR_REGION \
     --query SecretBinary \
     --output text | base64 -d > /tmp/test-munge.key

   md5sum /tmp/test-munge.key
   ```

#### Issue: Time Skew

**Symptom**: Munge auth fails with "Credential expired"

**Diagnostic**:
```bash
# On AWS instance, check time sync
chronyc tracking

# Compare time with headnode
ssh headnode date; date
```

**Solution**:
```bash
# On AWS instance (should be in AMI)
sudo yum install -y chrony
sudo systemctl enable chronyd
sudo systemctl start chronyd

# Verify sync
chronyc tracking
# Look for "System time: 0.000xxx seconds" (near zero)
```

### Slurm Version Mismatch

#### Issue: Version Incompatibility

**Symptom**: Nodes join cluster but slurmd crashes, logs show "Protocol version mismatch"

**Diagnostic**:
```bash
# Check headnode version
slurmctld --version

# SSH to AWS instance and check
/nfs/slurm/sbin/slurmd --version

# Versions MUST match exactly
```

**Solution**: Rebuild AMI with exact matching Slurm version. See [AMI Building Guide](onprem-to-aws-bursting.md#step-4-build-aws-ami-with-matching-slurm-version).

### User ID/Group Mismatch

#### Issue: File Permissions Wrong

**Symptom**: Jobs can't read/write files, "Permission denied" errors

**Diagnostic**:
```bash
# Check UID/GID on headnode
id jdoe

# Check UID/GID on AWS instance (SSH in)
id jdoe

# Must match exactly
```

**Solution**: Configure user management (SSSD/LDAP/NIS) on AMI. See [User Identity Guide](onprem-to-aws-bursting.md#step-2-ensure-user-identity-consistency).

---

## Instance Launch Issues

### Nodes Stuck in alloc# State

**Symptom**: Nodes remain in `alloc#` state and never become `alloc` or `idle`

**This is the most common issue.** It means instances are launching but not successfully joining the cluster.

**Diagnostic steps**:

1. **Check plugin logs**:
   ```bash
   sudo tail -100 /var/log/slurm/aws_plugin.log | grep ERROR
   ```

2. **Check if instances actually launched**:
   ```bash
   aws ec2 describe-instances \
     --filters "Name=tag:Name,Values=cloud-burst-*" \
               "Name=instance-state-name,Values=pending,running,stopped,stopping" \
     --query "Reservations[].Instances[].[InstanceId,State.Name,StateReason.Message,PrivateIpAddress]" \
     --output table
   ```

3. **If no instances**: Check CreateFleet errors:
   ```bash
   # Look for recent CreateFleet calls
   aws cloudtrail lookup-events \
     --lookup-attributes AttributeKey=EventName,AttributeValue=CreateFleet \
     --max-results 5 \
     --query 'Events[*].CloudTrailEvent' \
     --output text | jq -r '. | fromjson'
   ```

4. **If instances exist**, check console output:
   ```bash
   INSTANCE_ID=$(aws ec2 describe-instances \
     --filters "Name=tag:Name,Values=cloud-burst-0" \
               "Name=instance-state-name,Values=running" \
     --query "Reservations[0].Instances[0].InstanceId" \
     --output text)

   aws ec2 get-console-output --instance-id $INSTANCE_ID --output text
   ```

   Look for:
   - NFS mount errors
   - Munge key retrieval failures
   - slurmd startup failures
   - Network connectivity issues

5. **Test connectivity** from headnode to instance:
   ```bash
   ping <instance-private-ip>
   nc -zv <instance-private-ip> 6818  # Slurmd port
   ```

**Common causes and solutions**:

- **VPN down**: See [VPN Connectivity Issues](#vpn-connectivity-issues)
- **NFS mount fails**: See [NFS Mount Failures](#nfs-mount-failures)
- **Munge auth fails**: See [Munge Authentication Failures](#munge-authentication-failures)
- **Version mismatch**: See [Slurm Version Mismatch](#slurm-version-mismatch)
- **Security group**: Ensure allows traffic from on-prem CIDR
- **ResumeTimeout too short**: Increase in config.json (default 600s)

### Nodes Go to down~ State

**Symptom**: Nodes go from `alloc#` to `down~` without ever becoming available

**Cause**: ResumeTimeout exceeded - Slurm gave up waiting for node

**Solutions**:

1. **Increase ResumeTimeout**:
   ```bash
   # Edit config.json
   sudo vi /etc/slurm/aws-plugin/config.json
   # Change: "ResumeTimeout": 900  (15 minutes)

   # Regenerate slurm.conf.aws
   cd /etc/slurm/aws-plugin
   sudo ./generate_conf.py

   # Update slurm.conf and reconfigure
   sudo scontrol reconfigure
   ```

2. **Optimize AMI boot time**:
   - Pre-bake more into AMI (less in user data)
   - Use faster instance types
   - Minimize slurm-init.sh script

3. **Fix underlying issue** causing slow boot (see [Nodes Stuck in alloc#](#nodes-stuck-in-alloc-state))

### Launch Template or AMI Issues

#### Issue: "InvalidAMIID.NotFound"

**Symptom**: CreateFleet fails with AMI not found error

**Solutions**:

1. **Verify AMI exists and is in correct region**:
   ```bash
   aws ec2 describe-images \
     --image-ids ami-xxxxx \
     --region YOUR_REGION
   ```

2. **Check AMI is not deregistered**:
   ```bash
   aws ec2 describe-images \
     --owners self \
     --query 'Images[*].[ImageId,Name,State]' \
     --output table
   ```

3. **Verify launch template references correct AMI**:
   ```bash
   aws ec2 describe-launch-template-versions \
     --launch-template-id lt-xxxxx \
     --versions $Latest
   ```

#### Issue: "Invalid IAM Instance Profile"

**Symptom**: Instances launch but fail immediately, state reason shows IAM error

**Solution**:
```bash
# Check instance profile exists
aws iam get-instance-profile \
  --instance-profile-name SlurmComputeNodeProfile

# Check role is added to profile
aws iam get-instance-profile \
  --instance-profile-name SlurmComputeNodeProfile \
  --query 'InstanceProfile.Roles'

# If empty, add role
aws iam add-role-to-instance-profile \
  --instance-profile-name SlurmComputeNodeProfile \
  --role-name SlurmComputeNodeRole
```

### Subnet or Network Issues

#### Issue: "InsufficientFreeAddressesInSubnet"

**Symptom**: CreateFleet fails, no instances launch

**Diagnostic**:
```bash
# Check subnet available IPs
aws ec2 describe-subnets \
  --subnet-ids subnet-xxxxx \
  --query 'Subnets[0].[SubnetId,AvailableIpAddressCount,CidrBlock]' \
  --output table
```

**Solutions**:

1. **Add more subnets** to node group:
   ```json
   "SubnetIds": [
     "subnet-11111111",
     "subnet-22222222",
     "subnet-33333333"
   ]
   ```

2. **Terminate unused instances**:
   ```bash
   aws ec2 describe-instances \
     --filters "Name=subnet-id,Values=subnet-xxxxx" \
               "Name=instance-state-name,Values=running" \
     --query 'Reservations[].Instances[].[InstanceId,Tags[?Key==`Name`].Value|[0]]'
   ```

3. **Create larger subnet**: Use /23 or /22 instead of /24

---

## Runtime Issues

### Nodes Not Suspending

**Symptom**: Idle nodes don't terminate after SuspendTime

**Diagnostic**:

1. **Check if nodes are actually idle**:
   ```bash
   sinfo -N -p cloud
   scontrol show node cloud-burst-0
   ```

2. **Verify SuspendTime is configured**:
   ```bash
   scontrol show config | grep SuspendTime
   ```

3. **Check suspend.py is configured**:
   ```bash
   scontrol show config | grep SuspendProgram
   ```

4. **Check plugin logs**:
   ```bash
   sudo grep suspend /var/log/slurm/aws_plugin.log
   ```

**Solutions**:

1. **Manually test suspend**:
   ```bash
   echo "cloud-burst-0" | sudo -u slurm /etc/slurm/aws-plugin/suspend.py

   # Check logs for errors
   sudo tail -20 /var/log/slurm/aws_plugin.log
   ```

2. **Verify SuspendTime** in slurm.conf:
   ```bash
   grep SuspendTime /etc/slurm/slurm.conf
   # Should see: SuspendTime=900 (or your configured value)
   ```

### Instances Not Terminated

**Symptom**: Slurm shows nodes in `idle~`, but EC2 instances still running (costing money!)

**Diagnostic**:
```bash
# Check for orphaned instances
aws ec2 describe-instances \
  --filters "Name=tag:ManagedBy,Values=Slurm" \
            "Name=instance-state-name,Values=running" \
  --query "Reservations[].Instances[].[InstanceId,LaunchTime,Tags[?Key=='Name'].Value|[0],PrivateIpAddress]" \
  --output table
```

**Common causes**:

1. **Name tag missing or incorrect**:
   ```bash
   # Check instance Name tags
   aws ec2 describe-instances \
     --instance-ids i-xxxxx \
     --query 'Reservations[0].Instances[0].Tags[?Key==`Name`].Value' \
     --output text

   # Should match Slurm node name exactly
   ```

2. **suspend.py can't find instance**:
   - Check plugin logs for "Instance not found" errors
   - Verify region matches in partitions.json

3. **IAM permissions missing**:
   ```bash
   # Test terminate permission
   aws ec2 terminate-instances --dry-run --instance-ids i-xxxxx
   ```

**Solution - manually terminate orphaned instances**:
```bash
# Terminate specific instance
aws ec2 terminate-instances --instance-ids i-xxxxx

# Update Slurm node state
scontrol update NodeName=cloud-burst-0 State=DOWN Reason="manual cleanup"
scontrol update NodeName=cloud-burst-0 State=POWER_DOWN
```

### Nodes Stuck in down* State

**Symptom**: Nodes remain in `down*` state and never return to `idle~`

**Cause**: change_state.py not running or failing

**Solutions**:

1. **Check cron job**:
   ```bash
   sudo crontab -l | grep change_state
   # Should see: * * * * * /etc/slurm/aws-plugin/change_state.py &>/dev/null
   ```

2. **Add cron job if missing**:
   ```bash
   sudo crontab -e
   # Add: * * * * * /etc/slurm/aws-plugin/change_state.py &>/dev/null
   ```

3. **Manually run change_state.py**:
   ```bash
   sudo -u slurm /etc/slurm/aws-plugin/change_state.py

   # Check if nodes are cleaned up
   sinfo -p cloud
   ```

4. **Check cron logs for errors**:
   ```bash
   sudo grep change_state /var/log/cron
   sudo tail -50 /var/log/slurm/aws_plugin.log | grep change_state
   ```

---

## Compute Node Issues

### slurmd Won't Start

**Symptom**: slurmd service fails on compute node

**Diagnostic**:
```bash
# SSH to compute node
sudo systemctl status slurmd
sudo journalctl -u slurmd -n 50
```

**Common causes**:

1. **Slurm version mismatch**: See [Slurm Version Mismatch](#slurm-version-mismatch)

2. **NFS not mounted**:
   ```bash
   mount | grep nfs
   ls -la /nfs/slurm/sbin/slurmd
   ```

3. **Munge not running**:
   ```bash
   sudo systemctl status munge
   sudo journalctl -u munge
   ```

4. **Wrong node name**:
   ```bash
   /usr/local/bin/get_slurm_nodename
   # Should output Slurm node name, not hostname
   ```

5. **slurm.conf not accessible**:
   ```bash
   ls -la /etc/slurm/slurm.conf
   ```

6. **Network connectivity to headnode**:
   ```bash
   nc -zv <headnode-ip> 6817  # slurmctld port
   ```

**Solution - test slurmd manually**:
```bash
# Stop service
sudo systemctl stop slurmd

# Run in foreground with debug
sudo /nfs/slurm/sbin/slurmd -D -N $(hostname) -vvv
# Watch for errors

# If successful, restart service
sudo systemctl start slurmd
```

### Wrong Node Name

**Symptom**: Compute node registers with hostname instead of Slurm node name, shows as "unexpected" node

**Diagnostic**:
```bash
# On compute node, test get_slurm_nodename script
/usr/local/bin/get_slurm_nodename
# Should output: cloud-burst-N (not ip-10-1-1-X)
```

**Solutions**:

1. **Verify IMDSv2 is working**:
   ```bash
   # Get token
   TOKEN=$(curl -X PUT "http://169.254.169.254/latest/api/token" \
       -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")

   # Get Name tag
   curl -H "X-aws-ec2-metadata-token: $TOKEN" \
       http://169.254.169.254/latest/meta-data/tags/instance/Name
   ```

2. **Check launch template** has InstanceMetadataTags enabled:
   ```bash
   aws ec2 describe-launch-template-versions \
     --launch-template-id lt-xxxxx \
     --versions $Latest \
     --query 'LaunchTemplateVersions[0].LaunchTemplateData.MetadataOptions'
   ```

   Should show: `"InstanceMetadataTags": "enabled"`

3. **Verify IAM permissions** for ec2:DescribeTags:
   ```bash
   # On compute node
   aws ec2 describe-tags --filters "Name=resource-id,Values=$(ec2-metadata --instance-id | cut -d' ' -f2)"
   ```

4. **Check systemd service** uses get_slurm_nodename:
   ```bash
   cat /etc/systemd/system/slurmd.service | grep get_slurm_nodename
   ```

---

## Using the Connectivity Validator

For on-prem deployments, use the connectivity validator before deploying:

```bash
./scripts/validate-onprem-connectivity.sh \
  --headnode 10.0.1.100 \
  --region us-east-1 \
  --vpc vpc-xxxxx \
  --subnet subnet-xxxxx \
  --nfs-export /nfs
```

This tests:
- Network connectivity (VPN)
- NFS access
- Munge authentication
- IAM permissions

Run this BEFORE building your AMI to catch issues early.

---

## Logging and Debugging

### Enable Debug Logging

**In config.json**:
```json
{
  "LogLevel": "DEBUG",
  "LogFileName": "/var/log/slurm/aws_plugin.log"
}
```

Restart Slurm or wait for next resume/suspend event.

### Important Log Files

| Log File | Location | Contains |
|----------|----------|----------|
| Plugin logs | `/var/log/slurm/aws_plugin.log` | resume.py, suspend.py, change_state.py output |
| Slurmctld logs | `/var/log/slurm/slurmctld.log` | Slurm controller events |
| Slurmd logs (compute) | `/var/log/slurm/slurmd.log` | Compute node daemon |
| slurm-init script | `/var/log/slurm-init.log` | Boot-time initialization on compute nodes |
| System logs | `/var/log/messages` or `journalctl` | System-level errors |

### Check Specific Logs

```bash
# Plugin logs with timestamps
sudo tail -100 /var/log/slurm/aws_plugin.log | grep -E "ERROR|WARNING"

# Slurmctld logs for power save events
sudo grep -E "Power|Resume|Suspend" /var/log/slurm/slurmctld.log | tail -50

# On compute node - check boot initialization
ssh compute-node cat /var/log/slurm-init.log

# System journal for slurmd
ssh compute-node sudo journalctl -u slurmd -n 100
```

### Test Plugin Scripts Manually

```bash
# Test resume (as slurm user)
sudo -u slurm /etc/slurm/aws-plugin/resume.py cloud-burst-0

# Test suspend
sudo -u slurm /etc/slurm/aws-plugin/suspend.py cloud-burst-0

# Test change_state
sudo -u slurm /etc/slurm/aws-plugin/change_state.py

# Check logs after each test
sudo tail -20 /var/log/slurm/aws_plugin.log
```

### AWS CloudTrail

Enable CloudTrail to debug AWS API calls:
- View CreateFleet, DescribeInstances, TerminateInstances calls
- Check for throttling or authorization errors
- See which IAM principal made calls

```bash
# Query recent CloudTrail events
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=CreateFleet \
  --max-results 10 \
  --query 'Events[*].[EventTime,EventName,Username,ErrorCode]' \
  --output table
```

---

## Performance Issues

### Slow Instance Launches

**Symptom**: Nodes take > 5 minutes to become available

**Solutions**:

1. **Pre-bake more into AMI**:
   - Install software during AMI build, not in user data
   - Pre-configure services
   - Minimize slurm-init.sh script

2. **Use faster instance types**:
   - c5.xlarge launches faster than t3.medium
   - Newer generation instances boot faster

3. **Optimize NFS mounts**:
   - Use larger rsize/wsize: `rsize=1048576,wsize=1048576`
   - Consider EFS for cloud-native NFS

4. **Increase ResumeRate** if launching many nodes:
   ```json
   "ResumeRate": 100
   ```

### EC2 API Rate Limiting

**Symptom**: Errors about exceeding API rate limits, not all requested nodes launch

**Solutions**:

1. **Decrease launch rate**:
   ```json
   "ResumeRate": 25,
   "SuspendRate": 25
   ```

2. **Use EC2 Fleet instead of RunInstances** (plugin already does this)

3. **Request rate limit increase** from AWS Support

4. **Stagger job submissions** to avoid burst launches

### High Costs

**Symptom**: AWS bill higher than expected

**Diagnostic**:
```bash
# Check for orphaned running instances
aws ec2 describe-instances \
  --filters "Name=tag:ManagedBy,Values=Slurm" \
            "Name=instance-state-name,Values=running" \
  --query "Reservations[].Instances[].[InstanceId,InstanceType,LaunchTime,State.Name]" \
  --output table

# Check instance uptime
aws ec2 describe-instances \
  --filters "Name=tag:ManagedBy,Values=Slurm" \
  --query "Reservations[].Instances[].[InstanceId,LaunchTime,State.Name]" \
  --output table
```

**Solutions**:

1. **Ensure nodes are suspending**: See [Nodes Not Suspending](#nodes-not-suspending)

2. **Reduce SuspendTime** if jobs are short:
   ```bash
   # In config.json
   "SuspendTime": 300  # 5 minutes instead of 15
   ```

3. **Use Spot instances**:
   ```json
   "PurchasingOption": "spot",
   "SpotOptions": {
     "AllocationStrategy": "price-capacity-optimized"
   }
   ```

4. **Right-size instances**:
   ```bash
   # Check actual resource usage
   sacct -X -o JobID,NodeList,ReqCPUS,ReqMem,MaxRSS,CPUTime,Elapsed
   ```

5. **Set up cost alerts** in AWS Budgets

---

## Getting Help

If you're still stuck after trying the above:

1. **Gather diagnostic information**:
   ```bash
   # Plugin logs
   sudo tail -200 /var/log/slurm/aws_plugin.log > plugin-logs.txt

   # Slurm state
   sinfo -a > slurm-state.txt
   scontrol show node cloud-burst-0 > node-info.txt

   # AWS instances
   aws ec2 describe-instances \
     --filters "Name=tag:ManagedBy,Values=Slurm" \
     --output json > instances.json

   # Configuration (redact secrets!)
   cat /etc/slurm/aws-plugin/partitions.json > partitions.json
   cat /etc/slurm/aws-plugin/config.json > config.json
   ```

2. **Check existing issues**: https://github.com/scttfrdmn/aws-plugin-for-slurm/issues

3. **Open a new issue** with:
   - Plugin version (git commit or release)
   - Slurm version (on-prem and AMI)
   - Deployment type (all-AWS or on-prem → AWS)
   - Network setup (VPN/Direct Connect)
   - Diagnostic information from step 1
   - Steps to reproduce

4. **Review documentation**:
   - [On-Premises to AWS Guide](onprem-to-aws-bursting.md)
   - [Configuration Reference](configuration.md)
   - [Security Guide](security.md)

5. **AWS Support**: For AWS-specific issues (quotas, service limits, VPN), contact AWS Support

6. **SchedMD**: For Slurm-specific issues, consult [Slurm documentation](https://slurm.schedmd.com/) or SchedMD support
