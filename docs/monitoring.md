# Monitoring and Observability Guide

This document covers monitoring, logging, and observability for the AWS Plugin for Slurm.

## Overview

Effective monitoring involves:
- Plugin operational metrics
- Slurm cluster health
- AWS resource utilization
- Cost tracking
- Performance metrics
- Security events

## Plugin Monitoring

### Plugin Logs

**Default location**: Configured in `config.json`

```json
{
  "LogFileName": "/var/log/slurm/aws_plugin.log",
  "LogLevel": "INFO"
}
```

**Log Levels:**
- `DEBUG` - Detailed execution trace (development)
- `INFO` - Normal operations (production default)
- `WARNING` - Potential issues
- `ERROR` - Operation failures
- `CRITICAL` - Fatal errors

### Important Log Messages

**Successful operations:**
```
INFO - Launched node aws-node-0 i-0123456789abcdef0 10.0.10.5
INFO - Terminated instance aws-node-0 i-0123456789abcdef0
```

**Errors to monitor:**
```
ERROR - Failed to launch nodes for partition=aws and nodegroup=node
WARNING - Failed to launch 5 nodes
WARNING - EC2 Fleet error codes: InsufficientInstanceCapacity
ERROR - RequestLimitExceeded
```

### Shipping Logs to CloudWatch

**Install CloudWatch agent:**

```bash
sudo yum install amazon-cloudwatch-agent -y
```

**Configure agent** (`/opt/aws/amazon-cloudwatch-agent/etc/config.json`):

```json
{
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "/var/log/slurm/aws_plugin.log",
            "log_group_name": "/aws/slurm/plugin",
            "log_stream_name": "{instance_id}",
            "timezone": "UTC"
          },
          {
            "file_path": "/var/log/slurm/slurmctld.log",
            "log_group_name": "/aws/slurm/slurmctld",
            "log_stream_name": "{instance_id}"
          }
        ]
      }
    }
  }
}
```

**Start agent:**

```bash
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a fetch-config \
  -m ec2 \
  -s \
  -c file:/opt/aws/amazon-cloudwatch-agent/etc/config.json
```

**IAM permissions required:**

```json
{
  "Effect": "Allow",
  "Action": [
    "logs:CreateLogGroup",
    "logs:CreateLogStream",
    "logs:PutLogEvents",
    "logs:DescribeLogStreams"
  ],
  "Resource": "arn:aws:logs:*:*:log-group:/aws/slurm/*"
}
```

### CloudWatch Logs Insights Queries

**Count errors by type:**

```sql
fields @timestamp, @message
| filter @message like /ERROR/
| parse @message "*ERROR*" as prefix, error_msg
| stats count() by error_msg
| sort count() desc
```

**Track launch failures:**

```sql
fields @timestamp, @message
| filter @message like /Failed to launch/
| parse @message "Failed to launch * nodes" as failed_count
| stats sum(failed_count) as total_failed by bin(5m)
```

**API throttling:**

```sql
fields @timestamp, @message
| filter @message like /RequestLimitExceeded/
| stats count() as throttle_events by bin(1h)
```

**Launch times:**

```sql
fields @timestamp, @message
| filter @message like /Launched node/
| parse @message "Launched node * * *" as node_name, instance_id, ip
| stats count() as launches by bin(5m)
```

## Slurm Monitoring

### Key Metrics to Monitor

**Slurm daemon health:**
```bash
# Check slurmctld status
systemctl status slurmctld

# Check slurmdbd (if using accounting)
systemctl status slurmdbd
```

**Node states:**
```bash
# Count nodes by state
sinfo -o "%T" | sort | uniq -c

# Expected output:
#   100 idle~      (powered down, normal)
#    10 alloc      (running jobs)
#     5 idle       (available, waiting for jobs)
#     2 down*      (failed to respond, needs investigation)
```

**Job queue:**
```bash
# Jobs waiting for resources
squeue -t PD -o "%.18i %.9P %.8u %.2t %.10M %.6D %R"

# Running jobs
squeue -t R -o "%.18i %.9P %.8u %.2t %.10M %.6D %R"
```

**Scheduler performance:**
```bash
# Scheduler cycle time (should be < 2 seconds)
grep "sched: SchedulerCycleTime" /var/log/slurm/slurmctld.log | tail -1
```

### Slurm Metrics Collection

**Enable Slurm profiling:**

Add to `slurm.conf`:

```bash
JobAcctGatherType=jobacct_gather/linux
JobAcctGatherFrequency=30
```

**Query job statistics:**

```bash
# Show job resource usage
sacct -X --format=JobID,JobName,Partition,User,AllocCPUS,State,ExitCode,Start,End,ElapsedTime,CPUTime,MaxRSS

# Average wait time
sacct --format=JobID,Submit,Start,End --state=COMPLETED | \
  awk '{wait=$(NF-2)-$(NF-1); sum+=wait; count++} END {print "Avg wait:", sum/count, "seconds"}'
```

**Cluster utilization:**

```bash
# CPU utilization
sstat --format=AveCPU,AveRSS,AveVMSize --allsteps -j JOBID

# Node utilization
squeue -o "%C" | awk '{sum+=$1} END {print "Total CPUs allocated:", sum}'
```

### Exporting to CloudWatch

**Custom metrics script:**

```bash
#!/bin/bash
# /usr/local/bin/slurm_metrics_to_cloudwatch.sh

NAMESPACE="Slurm/Cluster"
REGION="us-east-1"

# Count nodes by state
IDLE_COUNT=$(sinfo -h -o "%T" | grep -c "^idle$")
ALLOC_COUNT=$(sinfo -h -o "%T" | grep -c "^alloc")
DOWN_COUNT=$(sinfo -h -o "%T" | grep -c "down")

# Send to CloudWatch
aws cloudwatch put-metric-data \
  --namespace "$NAMESPACE" \
  --metric-name IdleNodes \
  --value $IDLE_COUNT \
  --region $REGION

aws cloudwatch put-metric-data \
  --namespace "$NAMESPACE" \
  --metric-name AllocatedNodes \
  --value $ALLOC_COUNT \
  --region $REGION

aws cloudwatch put-metric-data \
  --namespace "$NAMESPACE" \
  --metric-name DownNodes \
  --value $DOWN_COUNT \
  --region $REGION

# Job queue length
PENDING_JOBS=$(squeue -t PD -h | wc -l)
RUNNING_JOBS=$(squeue -t R -h | wc -l)

aws cloudwatch put-metric-data \
  --namespace "$NAMESPACE" \
  --metric-name PendingJobs \
  --value $PENDING_JOBS \
  --region $REGION

aws cloudwatch put-metric-data \
  --namespace "$NAMESPACE" \
  --metric-name RunningJobs \
  --value $RUNNING_JOBS \
  --region $REGION
```

**Run via cron:**

```bash
# Add to crontab
* * * * * /usr/local/bin/slurm_metrics_to_cloudwatch.sh
```

## AWS Resource Monitoring

### EC2 Instance Metrics

**Default CloudWatch metrics** (5-minute intervals):
- CPUUtilization
- NetworkIn / NetworkOut
- DiskReadBytes / DiskWriteBytes
- StatusCheckFailed

**Enable detailed monitoring** (1-minute intervals):

```bash
aws ec2 monitor-instances --instance-ids i-xxxxx
```

**Or in launch template:**

```json
{
  "Monitoring": {
    "Enabled": true
  }
}
```

**Cost**: $2.10/instance/month for detailed monitoring

### EC2 Fleet Metrics

Track fleet request metrics:

```bash
# Describe fleet
aws ec2 describe-fleets \
  --fleet-ids fleet-xxxxx \
  --query 'Fleets[0].[FulfilledCapacity,TotalTargetCapacity,OnDemandFulfilledCapacity,SpotFulfilledCapacity]'
```

**Monitor fleet errors:**

```bash
aws ec2 describe-fleet-history \
  --fleet-id fleet-xxxxx \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ) \
  --event-type error
```

### Spot Instance Interruptions

**Monitor interruption rate:**

```bash
# Check CloudTrail for TerminateInstances events
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=TerminateInstances \
  --max-results 50 \
  --query 'Events[?contains(CloudTrailEvent, `spot-instance-termination`)]'
```

**Spot interruption metrics** (automatic):
- Available in CloudWatch under `AWS/EC2Spot` namespace
- `SpotInstanceInterruptions` metric

### VPC Flow Logs

Enable flow logs for network troubleshooting:

```bash
aws ec2 create-flow-logs \
  --resource-type VPC \
  --resource-ids vpc-xxxxx \
  --traffic-type ALL \
  --log-destination-type cloud-watch-logs \
  --log-group-name /aws/vpc/slurm-cluster
```

**Query flow logs for Slurm traffic:**

```sql
fields @timestamp, srcAddr, dstAddr, srcPort, dstPort, bytes
| filter dstPort = 6817 or dstPort = 6818
| stats sum(bytes) as totalBytes by dstAddr
| sort totalBytes desc
```

## CloudWatch Dashboards

### Create Slurm Cluster Dashboard

**Dashboard JSON:**

```json
{
  "widgets": [
    {
      "type": "metric",
      "properties": {
        "metrics": [
          [ "Slurm/Cluster", "IdleNodes" ],
          [ ".", "AllocatedNodes" ],
          [ ".", "DownNodes" ]
        ],
        "period": 300,
        "stat": "Average",
        "region": "us-east-1",
        "title": "Node States",
        "yAxis": {
          "left": {
            "min": 0
          }
        }
      }
    },
    {
      "type": "metric",
      "properties": {
        "metrics": [
          [ "Slurm/Cluster", "PendingJobs" ],
          [ ".", "RunningJobs" ]
        ],
        "period": 60,
        "stat": "Average",
        "region": "us-east-1",
        "title": "Job Queue"
      }
    },
    {
      "type": "log",
      "properties": {
        "query": "SOURCE '/aws/slurm/plugin'\n| fields @timestamp, @message\n| filter @message like /ERROR/\n| sort @timestamp desc\n| limit 20",
        "region": "us-east-1",
        "title": "Plugin Errors"
      }
    }
  ]
}
```

**Create dashboard:**

```bash
aws cloudwatch put-dashboard \
  --dashboard-name SlurmCluster \
  --dashboard-body file://dashboard.json
```

### Example Dashboard Layout

```
┌─────────────────────────────────────────────────────────┐
│                    Slurm Cluster                        │
├──────────────────────────┬──────────────────────────────┤
│  Node States             │  Job Queue                   │
│  ┌────────────────────┐  │  ┌────────────────────────┐  │
│  │ Idle: 85           │  │  │ Pending: 15            │  │
│  │ Allocated: 15      │  │  │ Running: 20            │  │
│  │ Down: 0            │  │  │ Completed: 150         │  │
│  └────────────────────┘  │  └────────────────────────┘  │
├──────────────────────────┴──────────────────────────────┤
│  CPU Utilization (avg across cluster)                   │
│  ┌─────────────────────────────────────────────────────┐│
│  │ [Graph: 60% utilization]                            ││
│  └─────────────────────────────────────────────────────┘│
├──────────────────────────────────────────────────────────┤
│  Recent Errors (Plugin Logs)                            │
│  ┌─────────────────────────────────────────────────────┐│
│  │ 15:32 ERROR Failed to launch nodes                  ││
│  │ 14:15 WARNING RequestLimitExceeded                  ││
│  └─────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────┘
```

## CloudWatch Alarms

### Critical Alarms

**Headnode down:**

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name slurm-headnode-down \
  --alarm-description "Slurm headnode status check failed" \
  --metric-name StatusCheckFailed_System \
  --namespace AWS/EC2 \
  --statistic Maximum \
  --period 60 \
  --threshold 1 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 2 \
  --dimensions Name=InstanceId,Value=i-headnode-xxxxx \
  --alarm-actions arn:aws:sns:us-east-1:ACCOUNT:slurm-alerts
```

**Too many down nodes:**

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name slurm-down-nodes \
  --metric-name DownNodes \
  --namespace Slurm/Cluster \
  --statistic Average \
  --period 300 \
  --threshold 5 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 2 \
  --alarm-actions arn:aws:sns:us-east-1:ACCOUNT:slurm-alerts
```

**Plugin errors:**

```bash
aws logs put-metric-filter \
  --log-group-name /aws/slurm/plugin \
  --filter-name PluginErrors \
  --filter-pattern "[time, level=ERROR*, ...]" \
  --metric-transformations \
    metricName=PluginErrorCount,metricNamespace=Slurm/Plugin,metricValue=1

aws cloudwatch put-metric-alarm \
  --alarm-name slurm-plugin-errors \
  --metric-name PluginErrorCount \
  --namespace Slurm/Plugin \
  --statistic Sum \
  --period 300 \
  --threshold 10 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 1 \
  --alarm-actions arn:aws:sns:us-east-1:ACCOUNT:slurm-alerts
```

**API throttling:**

```bash
aws logs put-metric-filter \
  --log-group-name /aws/slurm/plugin \
  --filter-name APIThrottling \
  --filter-pattern "RequestLimitExceeded" \
  --metric-transformations \
    metricName=APIThrottleCount,metricNamespace=Slurm/Plugin,metricValue=1

aws cloudwatch put-metric-alarm \
  --alarm-name slurm-api-throttling \
  --metric-name APIThrottleCount \
  --namespace Slurm/Plugin \
  --statistic Sum \
  --period 300 \
  --threshold 5 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 1 \
  --alarm-actions arn:aws:sns:us-east-1:ACCOUNT:slurm-alerts
```

### Warning Alarms

**High pending job queue:**

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name slurm-high-pending-jobs \
  --metric-name PendingJobs \
  --namespace Slurm/Cluster \
  --statistic Average \
  --period 600 \
  --threshold 50 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 2 \
  --alarm-actions arn:aws:sns:us-east-1:ACCOUNT:slurm-warnings
```

**Low idle capacity:**

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name slurm-low-idle-capacity \
  --metric-name IdleNodes \
  --namespace Slurm/Cluster \
  --statistic Average \
  --period 300 \
  --threshold 5 \
  --comparison-operator LessThanThreshold \
  --evaluation-periods 3 \
  --alarm-actions arn:aws:sns:us-east-1:ACCOUNT:slurm-warnings
```

## Performance Monitoring

### Launch Time Tracking

Add custom metrics to `resume.py`:

```python
import time
import boto3

cloudwatch = boto3.client('cloudwatch')

start_time = time.time()
# ... launch instances ...
launch_time = time.time() - start_time

cloudwatch.put_metric_data(
    Namespace='Slurm/Performance',
    MetricData=[{
        'MetricName': 'InstanceLaunchTime',
        'Value': launch_time,
        'Unit': 'Seconds',
        'Dimensions': [
            {'Name': 'Partition', 'Value': partition_name},
            {'Name': 'NodeGroup', 'Value': nodegroup_name}
        ]
    }]
)
```

**Query launch times:**

```sql
SELECT AVG(InstanceLaunchTime) as avg_launch,
       MAX(InstanceLaunchTime) as max_launch,
       COUNT(*) as launch_count
FROM SCHEMA("Slurm/Performance", InstanceLaunchTime)
WHERE time > ago(1h)
GROUP BY Partition, NodeGroup
```

### Job Completion Metrics

Export from Slurm accounting:

```bash
#!/bin/bash
# /usr/local/bin/export_job_metrics.sh

# Get completed jobs in last hour
sacct --format=JobID,Elapsed,Start,End,State,ExitCode --parsable2 \
  --starttime $(date -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) | \
while IFS='|' read -r jobid elapsed start end state exitcode; do
  if [ "$state" = "COMPLETED" ]; then
    # Parse elapsed time to seconds
    elapsed_sec=$(echo $elapsed | awk -F: '{ print ($1 * 3600) + ($2 * 60) + $3 }')

    aws cloudwatch put-metric-data \
      --namespace Slurm/Jobs \
      --metric-name JobDuration \
      --value $elapsed_sec \
      --unit Seconds
  fi
done
```

## Third-Party Monitoring Tools

### Prometheus + Grafana

**Slurm Exporter:**

```bash
# Install Slurm Prometheus exporter
wget https://github.com/vpenso/prometheus-slurm-exporter/releases/download/0.20/prometheus-slurm-exporter-0.20.linux-amd64.tar.gz
tar -xzf prometheus-slurm-exporter-*.tar.gz
sudo mv prometheus-slurm-exporter /usr/local/bin/

# Create systemd service
sudo cat > /etc/systemd/system/prometheus-slurm-exporter.service <<EOF
[Unit]
Description=Prometheus Slurm Exporter
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/prometheus-slurm-exporter

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable --now prometheus-slurm-exporter
```

**Metrics exposed:**
- `slurm_nodes_total`
- `slurm_nodes_idle`
- `slurm_nodes_allocated`
- `slurm_jobs_pending`
- `slurm_jobs_running`
- `slurm_scheduler_cycle_time`

**Grafana Dashboard:**

Import dashboard ID: 4323 (Slurm Dashboard by vpenso)

### Datadog

**Install Datadog agent:**

```bash
DD_API_KEY=YOUR_KEY bash -c "$(curl -L https://s3.amazonaws.com/dd-agent/scripts/install_script.sh)"
```

**Configure Slurm integration:**

```yaml
# /etc/datadog-agent/conf.d/slurm.d/conf.yaml
init_config:

instances:
  - squeue_path: /usr/bin/squeue
    sinfo_path: /usr/bin/sinfo
```

### ELK Stack (Elasticsearch, Logstash, Kibana)

**Logstash pipeline for Slurm logs:**

```ruby
# /etc/logstash/conf.d/slurm.conf
input {
  file {
    path => "/var/log/slurm/*.log"
    start_position => "beginning"
  }
}

filter {
  grok {
    match => { "message" => "%{TIMESTAMP_ISO8601:timestamp} %{LOGLEVEL:level} %{GREEDYDATA:message}" }
  }
  date {
    match => [ "timestamp", "ISO8601" ]
  }
}

output {
  elasticsearch {
    hosts => ["localhost:9200"]
    index => "slurm-%{+YYYY.MM.dd}"
  }
}
```

## Troubleshooting with Monitoring

### Scenario: Nodes Not Launching

**Check:**

1. Plugin errors:
   ```bash
   grep ERROR /var/log/slurm/aws_plugin.log | tail -20
   ```

2. CloudWatch Logs Insights:
   ```sql
   fields @message
   | filter @message like /Failed to launch/
   | display @timestamp, @message
   ```

3. CloudTrail for EC2 API calls:
   ```bash
   aws cloudtrail lookup-events \
     --lookup-attributes AttributeKey=EventName,AttributeValue=CreateFleet \
     --max-results 10
   ```

### Scenario: High Costs

**Investigate:**

1. Check instance hours by type:
   ```bash
   aws ce get-cost-and-usage \
     --time-period Start=2025-09-01,End=2025-09-30 \
     --granularity DAILY \
     --metrics UnblendedCost \
     --group-by Type=DIMENSION,Key=INSTANCE_TYPE
   ```

2. Idle node analysis:
   ```bash
   # Nodes idle > 1 hour
   sinfo -o "%N %T %O" | awk '$2=="idle" && $3 > 3600'
   ```

3. Job efficiency:
   ```bash
   # Low CPU utilization jobs
   sacct --format=JobID,NodeList,CPUTimeRAW,TotalCPU --state=COMPLETED | \
     awk 'NR>2 {eff=$4/$3*100; if(eff<50) print $0, eff"%"}'
   ```

### Scenario: Performance Degradation

**Check:**

1. Scheduler cycle time:
   ```bash
   grep "SchedulerCycleTime" /var/log/slurm/slurmctld.log | \
     tail -10 | awk '{print $NF}'
   ```

2. Network latency:
   ```bash
   # From headnode to compute nodes
   for node in $(sinfo -N -h -o "%N" | head -5); do
     ping -c 3 $node | grep avg
   done
   ```

3. NFS performance:
   ```bash
   # On compute node
   dd if=/dev/zero of=/nfs/testfile bs=1M count=100
   ```

## Best Practices

1. **Enable detailed monitoring** for critical instances
2. **Ship all logs to CloudWatch** for centralized analysis
3. **Create dashboards** for at-a-glance cluster health
4. **Set up alarms** for critical issues (headnode down, API throttling)
5. **Monitor costs** alongside performance
6. **Regularly review metrics** and tune thresholds
7. **Use CloudWatch Logs Insights** for log analysis
8. **Export metrics to long-term storage** (S3) for historical analysis
9. **Document baseline performance** for comparison
10. **Automate alerts** to appropriate channels (email, Slack, PagerDuty)

## Monitoring Checklist

### Initial Setup
- [ ] CloudWatch agent installed on headnode
- [ ] Plugin logs shipped to CloudWatch
- [ ] Slurm logs shipped to CloudWatch
- [ ] Custom metrics script created for node states
- [ ] Custom metrics script created for job queue
- [ ] Dashboard created
- [ ] Critical alarms configured
- [ ] SNS topic created for alerts

### Ongoing Monitoring
- [ ] Check dashboard daily
- [ ] Review plugin logs weekly
- [ ] Analyze failed launches
- [ ] Track API throttling events
- [ ] Monitor cost trends
- [ ] Review job efficiency
- [ ] Check node utilization
- [ ] Verify alarm health

## Next Steps

- Review [Performance Tuning](performance-tuning.md) based on metrics
- Check [Troubleshooting](troubleshooting.md) for common issues
- Explore [Advanced Usage](advanced-usage.md) for complex scenarios
