#!/bin/bash
echo "$(date +%s) resume called with: $*" >> /rig/resume.log
for i in $(seq 1 300); do
  echo "$(date +%s) Sync launch: progress 0/4 (elapsed ${i}s)" >> /rig/resume.log
  sleep 1
done
echo "$(date +%s) resume finished (never registered nodes)" >> /rig/resume.log
