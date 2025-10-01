# Testing and Validation Guide

This document provides procedures for testing and validating the AWS Plugin for Slurm.

## Pre-Deployment Testing

### Configuration Validation

**Validate JSON syntax:**

```bash
# Test config.json
python3 -c "import json; json.load(open('config.json')); print('✓ config.json is valid')"

# Test partitions.json
python3 -c "import json; json.load(open('partitions.json')); print('✓ partitions.json is valid')"
```

**Validate configuration with plugin:**

```bash
cd /path/to/plugin
python3 -c "
import common
try:
    logger, config, partitions = common.get_common('test')
    print('✓ Configuration loaded successfully')
    print(f'  Partitions: {len(partitions)}')
    for p in partitions:
        print(f'    - {p[\"PartitionName\"]}: {len(p[\"NodeGroups\"])} node groups')
except Exception as e:
    print(f'✗ Configuration error: {e}')
    exit(1)
"
```

### AWS Permissions Testing

**Test headnode IAM permissions:**

```bash
# Test EC2 Fleet creation (dry-run)
aws ec2 create-fleet \
  --dry-run \
  --launch-template-configs '{"LaunchTemplateSpecification":{"LaunchTemplateId":"lt-xxxxx","Version":"$Latest"},"Overrides":[{"InstanceType":"t3.micro","SubnetId":"subnet-xxxxx"}]}' \
  --target-capacity-specification 'TotalTargetCapacity=1,DefaultTargetCapacityType=on-demand' \
  --type instant

# Should return: DryRunOperation error (which means permissions OK)
```

**Test instance termination permission:**

```bash
# Launch a test instance
TEST_INSTANCE=$(aws ec2 run-instances \
  --instance-type t3.micro \
  --image-id ami-xxxxx \
  --subnet-id subnet-xxxxx \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=test-slurm-plugin}]' \
  --query 'Instances[0].InstanceId' \
  --output text)

# Wait for it to start
aws ec2 wait instance-running --instance-ids $TEST_INSTANCE

# Test termination
aws ec2 terminate-instances --instance-ids $TEST_INSTANCE

echo "✓ IAM permissions validated"
```

### Network Connectivity Testing

**From headnode:**

```bash
# Test AWS API connectivity
curl -I https://ec2.us-east-1.amazonaws.com
# Should return 200 OK or 403 (forbidden, but reachable)

# Test subnet accessibility (if known compute node IP)
ping -c 3 10.0.10.5

# Test Slurm ports (if test node exists)
nc -zv 10.0.10.5 6818
```

## Post-Deployment Testing

### Basic Functionality Tests

#### Test 1: Single Node Launch

```bash
# Submit job for 1 node
srun -N1 -p aws hostname

# Expected: Node launches, job runs, outputs hostname
```

**Validation:**
- EC2 instance appears in console
- Node transitions: `idle~` → `alloc#` → `alloc`
- Job completes successfully
- After SuspendTime, node returns to `idle~` and instance terminates

**Check logs:**
```bash
# Plugin logs
grep "Launched node" /var/log/slurm/aws_plugin.log | tail -1

# Slurm logs
grep "sched: Allocate" /var/log/slurm/slurmctld.log | tail -1
```

#### Test 2: Multiple Node Launch

```bash
# Submit job for 5 nodes
srun -N5 -p aws hostname

# Monitor scaling
watch 'sinfo -p aws -o "%P %a %l %D %T %N"'
```

**Validation:**
- 5 EC2 instances launch
- All nodes become allocated
- Job runs on all 5 nodes
- Outputs 5 hostnames

#### Test 3: Node Suspend

```bash
# Run short job
srun -N1 -p aws sleep 10

# Wait for SuspendTime (e.g., 350 seconds)
sleep 360

# Check node state
sinfo -p aws

# Expected: Node returns to 'idle~' (powered down)
```

**Validation:**
- EC2 instance terminates
- Node shows as `idle~` in sinfo
- No errors in plugin logs

#### Test 4: Job Queue

```bash
# Submit multiple jobs
for i in {1..10}; do
  sbatch -N1 -p aws --wrap="sleep 30"
done

# Monitor queue
watch 'squeue -p aws'
```

**Validation:**
- Jobs queue correctly
- Nodes launch as needed (up to MaxNodes)
- Jobs complete successfully
- Nodes terminate after idle period

### Failure Scenarios

#### Test 5: Invalid Configuration

**Corrupt config.json:**

```bash
# Backup first
cp config.json config.json.backup

# Break JSON
echo "invalid" >> config.json

# Try to run
srun -N1 -p aws hostname

# Expected: Error in logs, job fails
```

**Restore:**
```bash
mv config.json.backup config.json
```

**Validation:**
- Plugin logs show configuration error
- Jobs don't launch
- Error is clear and actionable

#### Test 6: Insufficient Capacity

```bash
# Request instance type with limited capacity
srun -N100 -p aws --constraint=rare_instance_type hostname
```

**Validation:**
- Some nodes may fail to launch
- Plugin logs show "InsufficientInstanceCapacity"
- Partial launch is handled gracefully
- Available nodes still run jobs

#### Test 7: API Throttling

**Artificially trigger throttling:**

```bash
# Submit many jobs rapidly
for i in {1..100}; do
  sbatch -N1 -p aws --wrap="sleep 5" &
done
wait
```

**Validation:**
- Check for throttling in logs:
  ```bash
  grep "RequestLimitExceeded" /var/log/slurm/aws_plugin.log
  ```
- Plugin retries throttled requests
- Jobs eventually launch

### Performance Testing

#### Test 8: Launch Time Measurement

```bash
#!/bin/bash
# Measure time to launch 10 nodes

START=$(date +%s)

srun -N10 -p aws --exclusive hostname

END=$(date +%s)
DURATION=$((END - START))

echo "Launch time for 10 nodes: ${DURATION} seconds"
echo "Average per node: $((DURATION / 10)) seconds"
```

**Expected results:**
- First launch: 180-300 seconds (cold start)
- With pre-baked AMI: 120-180 seconds
- Subsequent launches (if nodes warm): 60-120 seconds

#### Test 9: Scale-Up Test

```bash
# Launch maximum nodes
srun -N100 -p aws --exclusive sleep 60 &

# Monitor progress
for i in {1..20}; do
  sleep 10
  ALLOC=$(sinfo -p aws -h -o "%T" | grep -c "alloc")
  echo "$(date +%T): $ALLOC nodes allocated"
done
```

**Validation:**
- Nodes launch at rate ≤ ResumeRate per minute
- All requested nodes eventually launch (if capacity available)
- No errors in logs

#### Test 10: Scale-Down Test

```bash
# Launch nodes, then let them idle
srun -N10 -p aws sleep 10

# Monitor suspension
watch 'sinfo -p aws -o "%T %O"'
```

**Validation:**
- After SuspendTime, nodes move to `idle~`
- EC2 instances terminate
- Termination occurs at rate ≤ SuspendRate per minute

### Integration Testing

#### Test 11: MPI Job

```bash
#!/bin/bash
#SBATCH -N4
#SBATCH -p aws
#SBATCH --ntasks-per-node=8

module load mpi

mpirun hostname
```

**Validation:**
- 4 nodes launch
- MPI communication works
- Job completes successfully
- Output shows all 32 ranks

#### Test 12: GPU Job (if applicable)

```bash
srun -N1 -p gpu --gres=gpu:1 nvidia-smi
```

**Validation:**
- GPU instance launches
- nvidia-smi shows GPU
- GRES allocation works
- Job completes

#### Test 13: Long-Running Job

```bash
sbatch -N2 -p aws --time=04:00:00 --wrap="sleep 14400"
```

**Validation:**
- Job runs for full duration
- Nodes stay allocated
- No premature termination
- Successful completion

### Security Testing

#### Test 14: IMDSv2 Verification

**On compute node:**

```bash
# Try IMDSv1 (should fail)
curl http://169.254.169.254/latest/meta-data/

# Try IMDSv2 (should succeed)
TOKEN=$(curl -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
curl -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/
```

**Validation:**
- IMDSv1 fails or returns error
- IMDSv2 succeeds
- Token-based access required

#### Test 15: Security Group Rules

**Test from external IP:**

```bash
# Try to connect to compute node on Slurm port (should fail)
nc -zv COMPUTE_NODE_PUBLIC_IP 6818

# Should timeout or be refused
```

**Validation:**
- Compute nodes not accessible from internet
- Only headnode can reach compute nodes on Slurm ports

#### Test 16: Munge Authentication

**On compute node:**

```bash
# Test munge
munge -n | unmunge

# Should output: STATUS: Success
```

**From headnode to compute:**

```bash
ssh compute-node "munge -n" | unmunge
```

**Validation:**
- Munge encryption/decryption works
- Authentication successful between nodes
- No "Credential replayed" errors

### Stress Testing

#### Test 17: Rapid Job Submission

```bash
#!/bin/bash
# Submit 1000 short jobs
for i in {1..1000}; do
  sbatch -N1 -p aws --wrap="sleep 1" &
  if [ $((i % 100)) -eq 0 ]; then
    wait
  fi
done
wait

echo "Submitted 1000 jobs"
```

**Validation:**
- Slurm scheduler handles load
- Plugin doesn't crash
- Jobs complete (may take time)
- No memory leaks

#### Test 18: Node Churn

```bash
# Repeatedly launch and terminate
for i in {1..20}; do
  echo "Iteration $i"
  srun -N5 -p aws sleep 10
  sleep $((SuspendTime + 60))
done
```

**Validation:**
- Nodes launch and terminate reliably
- No stuck nodes
- No leaked instances
- Plugin remains stable

### Monitoring Validation

#### Test 19: Log Shipping

**If using CloudWatch:**

```bash
# Submit test job
srun -N1 -p aws hostname

# Check CloudWatch Logs
aws logs tail /aws/slurm/plugin --follow

# Should see "Launched node" message
```

**Validation:**
- Logs appear in CloudWatch
- Timestamps are correct
- All log levels captured

#### Test 20: Metrics Collection

**If using custom metrics:**

```bash
# Trigger metric collection script
/usr/local/bin/slurm_metrics_to_cloudwatch.sh

# Check CloudWatch Metrics
aws cloudwatch get-metric-statistics \
  --namespace Slurm/Cluster \
  --metric-name IdleNodes \
  --start-time $(date -u -d '10 minutes ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 \
  --statistics Average
```

**Validation:**
- Metrics appear in CloudWatch
- Values are accurate
- Updates occur regularly

## Automated Testing Script

**Complete validation script:**

```bash
#!/bin/bash
# test_plugin.sh - Automated testing script

set -e

echo "=== AWS Plugin for Slurm Test Suite ==="
echo

# Test 1: Configuration
echo "Test 1: Configuration Validation"
python3 -c "import common; common.get_common('test')" && echo "✓ PASS" || echo "✗ FAIL"
echo

# Test 2: Single node launch
echo "Test 2: Single Node Launch"
srun -N1 -p aws --time=00:05:00 hostname && echo "✓ PASS" || echo "✗ FAIL"
echo

# Test 3: Multi-node launch
echo "Test 3: Multi-Node Launch"
srun -N3 -p aws --time=00:05:00 hostname | wc -l | grep -q 3 && echo "✓ PASS" || echo "✗ FAIL"
echo

# Test 4: Job queue
echo "Test 4: Job Queue"
for i in {1..5}; do sbatch -N1 -p aws --wrap="sleep 30" >/dev/null; done
sleep 5
PENDING=$(squeue -t PD -p aws -h | wc -l)
[ $PENDING -gt 0 ] && echo "✓ PASS ($PENDING jobs pending)" || echo "✗ FAIL"
echo

# Test 5: Node suspension
echo "Test 5: Node Suspension (this takes a while...)"
srun -N1 -p aws sleep 10
echo "Waiting for SuspendTime..."
sleep 360
POWERED_DOWN=$(sinfo -p aws -h -o "%T" | grep -c "idle~")
[ $POWERED_DOWN -gt 0 ] && echo "✓ PASS" || echo "✗ FAIL"
echo

echo "=== Test Suite Complete ==="
```

**Run tests:**

```bash
chmod +x test_plugin.sh
./test_plugin.sh 2>&1 | tee test_results.txt
```

## Continuous Testing

**Cron job for daily tests:**

```bash
# Add to crontab
0 2 * * * /path/to/test_plugin.sh >> /var/log/slurm/plugin_tests.log 2>&1
```

**Monitor test results:**

```bash
tail -f /var/log/slurm/plugin_tests.log
```

## Test Environment Best Practices

1. **Use separate test partition** - Don't disrupt production
2. **Small instance types** - t3.micro for tests
3. **Low MaxNodes** - Limit test capacity
4. **Short timeouts** - Fast test iterations
5. **Automated cleanup** - Remove test resources
6. **Regular testing** - Weekly or after changes
7. **Document failures** - Track issues
8. **Version control configs** - Test different configurations

## Troubleshooting Test Failures

### Configuration Tests Fail

- Check JSON syntax
- Verify all required fields present
- Check file permissions

### Node Launch Tests Fail

- Check IAM permissions
- Verify subnet IDs valid
- Check launch template exists
- Review plugin logs

### Suspend Tests Fail

- Verify SuspendTime configured
- Check change_state.py cron job
- Review termination permissions

### MPI Tests Fail

- Check security groups allow inter-node traffic
- Verify MPI installed on compute nodes
- Check hostfile generation

## Next Steps

- Set up [Monitoring](monitoring.md) for ongoing validation
- Review [Troubleshooting](troubleshooting.md) for common issues
- Implement [Performance Tuning](performance-tuning.md) based on results
