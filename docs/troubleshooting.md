# Troubleshooting Guide

Common issues and solutions for the AWS Plugin for Slurm.

## Installation Issues

### boto3 Import Error

**Symptom**: Plugin scripts fail with `ModuleNotFoundError: No module named 'boto3'`

**Solution**:
```bash
sudo pip3 install boto3
```

Make sure you're using the same Python version that Slurm uses to execute the scripts.

### AWS CLI Not Found

**Symptom**: Cannot configure AWS credentials

**Solution**:
```bash
sudo pip3 install awscli
```

### Permission Denied When Executing Scripts

**Symptom**: `bash: ./resume.py: Permission denied`

**Solution**:
```bash
chmod +x /path/to/plugin/*.py
```

## Configuration Issues

### Nodes Not Appearing in sinfo

**Symptom**: After running `generate_conf.py` and reconfiguring Slurm, nodes don't appear

**Possible causes**:
1. Slurm configuration not properly reloaded
   ```bash
   scontrol reconfigure
   # Or restart slurmctld
   sudo systemctl restart slurmctld
   ```

2. Configuration not appended to `slurm.conf`
   ```bash
   # Verify the nodes are defined in slurm.conf
   grep "NodeName=aws" /etc/slurm/slurm.conf
   ```

3. `PrivateData` not set to `CLOUD`
   ```bash
   # Check slurm.conf contains:
   grep "PrivateData=CLOUD" /etc/slurm/slurm.conf
   ```

### JSON Configuration Parse Error

**Symptom**: Plugin scripts fail with JSON parse error

**Solution**:
- Validate your JSON files using a JSON validator
- Common issues:
  - Trailing commas in arrays/objects
  - Unquoted strings
  - Missing commas between elements
  - Comments (JSON doesn't support comments)

## AWS Permission Issues

### Access Denied Errors

**Symptom**: Plugin logs show `botocore.exceptions.ClientError: An error occurred (UnauthorizedOperation)`

**Solutions**:

1. **If headnode is on AWS**: Verify IAM role is attached to the instance
   ```bash
   # Check if role is attached
   aws sts get-caller-identity
   ```

2. **If headnode is not on AWS**: Verify AWS credentials are configured
   ```bash
   # Test credentials
   aws sts get-caller-identity

   # If using named profile, test with:
   aws sts get-caller-identity --profile profile_name
   ```

3. Verify IAM policy includes all required permissions:
   - `ec2:CreateFleet`
   - `ec2:RunInstances`
   - `ec2:TerminateInstances`
   - `ec2:CreateTags`
   - `ec2:DescribeInstances`
   - `iam:PassRole`

### PassRole Error

**Symptom**: `User: arn:aws:iam::ACCOUNT:user/NAME is not authorized to perform: iam:PassRole`

**Solution**: Add `iam:PassRole` permission for the compute node IAM role to the headnode's IAM policy:

```json
{
  "Effect": "Allow",
  "Action": "iam:PassRole",
  "Resource": "arn:aws:iam::ACCOUNT_ID:role/ComputeNodeRole"
}
```

## Instance Launch Issues

### Nodes Stuck in POWER_UP

**Symptom**: Nodes remain in `POWER_UP` state and never become available

**Diagnostic steps**:

1. Check plugin logs for errors:
   ```bash
   tail -f /var/log/slurm/aws.log
   ```

2. Check if EC2 instances are actually launching:
   - Open EC2 console
   - Filter by the `Name` tag matching your node name
   - Check instance state and status checks

3. Verify `ResumeTimeout` is sufficient:
   - Consider instance launch time + user data script execution
   - Increase value in `config.json` if needed (e.g., 600 seconds)

4. Check EC2 Fleet errors in CloudTrail:
   - Look for `CreateFleet` API calls
   - Review error messages

### Nodes Move to DOWN State

**Symptom**: Nodes go from `POWER_UP` to `DOWN~` or `DOWN*`

**Common causes**:

1. **Exceeded ResumeTimeout**: Instance didn't respond in time
   - Increase `ResumeTimeout` in `config.json`
   - Verify user data scripts complete quickly
   - Check network connectivity between headnode and compute nodes

2. **slurmd Failed to Start on Compute Node**
   ```bash
   # SSH to compute node and check
   sudo systemctl status slurmd
   sudo journalctl -u slurmd
   ```

3. **Node Name Mismatch**
   - Verify `get_nodename` script returns correct name
   - Check `InstanceMetadataTags` is enabled in launch template
   - Verify `Name` tag is set on instance

4. **Network Connectivity Issues**
   - Verify security group allows Slurm ports (default: 6817-6819)
   - Check network ACLs and routing
   - Test connectivity: `telnet compute-node-ip 6818`

### Instance Launch Failures

**Symptom**: `CreateFleet` returns errors or zero instances

**Common causes**:

1. **InsufficientInstanceCapacity**
   - Try different instance types
   - Try different availability zones
   - Use instance type diversification in `LaunchTemplateOverrides`

2. **VPC/Subnet Configuration Issues**
   - Verify subnet IDs are valid
   - Check subnets have available IP addresses
   - Ensure subnets are in different AZs if using multiple subnets

3. **Launch Template Issues**
   - Verify launch template exists and version is correct
   - Check AMI is available in the region
   - Verify security group IDs are valid

4. **Spot Instance Issues**
   - Check spot instance pricing
   - Verify `SpotOptions` configuration
   - Consider using price diversification

### "Duplicate Instance Pools" Error

**Symptom**: `CreateFleet` fails with "The fleet configuration contains duplicate instance pools"

**Solution**: Ensure all subnets in `SubnetIds` are in different availability zones:

```json
"SubnetIds": [
  "subnet-11111111",  // us-east-1a
  "subnet-22222222"   // us-east-1b - different AZ
]
```

## Runtime Issues

### Nodes Not Suspending

**Symptom**: Idle nodes don't terminate after `SuspendTime`

**Diagnostic steps**:

1. Check if nodes are actually idle:
   ```bash
   sinfo
   scontrol show node node-name
   ```

2. Verify `SuspendTime` is configured:
   ```bash
   grep SuspendTime /etc/slurm/slurm.conf
   ```

3. Check plugin logs for suspend errors:
   ```bash
   tail -f /var/log/slurm/aws.log
   ```

4. Manually test suspend:
   ```bash
   /path/to/suspend.py partition-nodegroup-0
   ```

### Instance Not Terminated on Suspend

**Symptom**: Slurm marks node as powered down, but EC2 instance still running

**Common causes**:

1. **Name Tag Missing or Incorrect**
   - Check EC2 console for `Name` tag on instance
   - Verify tag value matches Slurm node name
   - Ensure plugin isn't overriding the `Name` tag

2. **suspend.py Cannot Find Instance**
   - Check plugin logs for errors
   - Verify headnode has `ec2:DescribeInstances` permission

3. **Region Mismatch**
   - Verify `Region` in `partitions.json` matches where instance launched
   - Check if using correct AWS profile

### Nodes Stuck in DOWN* State

**Symptom**: Nodes remain in `DOWN*` state and never return to `POWER_SAVING`

**Solution**:

1. Verify `change_state.py` is running via cron:
   ```bash
   sudo crontab -l
   # Should see: * * * * * /path/to/change_state.py &>/dev/null
   ```

2. Manually run `change_state.py` to clean up:
   ```bash
   /path/to/change_state.py
   ```

3. Check cron logs for errors:
   ```bash
   sudo grep change_state /var/log/cron
   ```

## Compute Node Issues

### slurmd Won't Start

**Symptom**: `slurmd` fails to start on compute nodes

**Diagnostic steps**:

1. Check slurmd status:
   ```bash
   sudo systemctl status slurmd
   sudo journalctl -u slurmd
   ```

2. Common issues:
   - Slurm version mismatch between headnode and compute node
   - `slurm.conf` not accessible or outdated
   - Incorrect node name
   - Network connectivity to headnode

3. Test slurmd manually:
   ```bash
   sudo /path/to/slurmd -D -N $(hostname)
   ```

### Wrong Node Name

**Symptom**: Compute node registers with hostname instead of Slurm node name

**Solution**:

1. Verify `get_nodename` script is working:
   ```bash
   /path/to/get_nodename
   # Should output: partition-nodegroup-N
   ```

2. Check instance metadata tags are enabled:
   ```bash
   curl http://169.254.169.254/latest/meta-data/tags/instance/Name
   ```

3. Verify systemd service is configured correctly in `/lib/systemd/system/slurmd.service`:
   ```ini
   ExecStartPre=/bin/bash -c "/bin/systemctl set-environment SLURM_NODENAME=$(/path/to/get_nodename)"
   ExecStart=/path/to/slurmd -N $SLURM_NODENAME $SLURMD_OPTIONS
   ```

4. Reload systemd after changes:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl restart slurmd
   ```

## Logging and Debugging

### Enable Debug Logging

Set `LogLevel` to `DEBUG` in `config.json`:

```json
{
  "LogLevel": "DEBUG",
  "LogFileName": "/var/log/slurm/aws.log",
  ...
}
```

### Check Slurm Logs

```bash
# Slurmctld logs
tail -f /var/log/slurm/slurmctld.log

# Slurmd logs (on compute nodes)
tail -f /var/log/slurm/slurmd.log
```

### Test Plugin Scripts Manually

```bash
# Test resume
/path/to/resume.py partition-nodegroup-0

# Test suspend
/path/to/suspend.py partition-nodegroup-0

# Test change_state
/path/to/change_state.py
```

### AWS CloudTrail

Enable CloudTrail to debug AWS API calls:
- View `CreateFleet`, `DescribeInstances`, `TerminateInstances` calls
- Check for throttling or authorization errors

## Performance Issues

### Slow Instance Launches

**Solutions**:
- Pre-bake software into AMI instead of using user data scripts
- Use faster instance types for compute nodes
- Increase `ResumeRate` if hitting rate limits slowly
- Use multiple node groups to parallelize launches

### Rate Limiting

**Symptom**: Errors about exceeding API rate limits

**Solutions**:
- Decrease `ResumeRate` and `SuspendRate` in `config.json`
- Request rate limit increases from AWS Support
- Stagger job submissions

## Getting Help

If you're still stuck:

1. Check plugin logs with debug logging enabled
2. Review Slurm documentation on [Power Saving](https://slurm.schedmd.com/power_save.html)
3. Check AWS service health dashboard
4. Open an issue on GitHub with:
   - Plugin logs
   - Slurm logs
   - Configuration files (redact sensitive info)
   - Steps to reproduce
