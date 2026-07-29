#!/bin/bash
echo "$(date +%s) resume called with: $*" >> /rig/resume.log
NODES=$(scontrol show hostnames "$1")
# Mimic resume.py's synchronous launch: wait, then register each node's address.
for i in $(seq 1 40); do
  echo "$(date +%s) Sync launch: progress 0/4 (elapsed ${i}s)" >> /rig/resume.log
  sleep 1
done
i=10
for n in $NODES; do
  scontrol update nodename=$n nodeaddr=10.0.0.$i nodehostname=$n \
    >> /rig/resume.log 2>&1 && echo "$(date +%s) registered $n" >> /rig/resume.log
  i=$((i+1))
done
echo "$(date +%s) all 4 nodes configured and ready" >> /rig/resume.log
