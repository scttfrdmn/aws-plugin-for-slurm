#!/bin/bash
# Reproduce the ResumeTimeout finding from issue #12 against a real slurmctld.
#
# The question: does Slurm mark cloud nodes DOWN at ResumeTimeout while ResumeProgram is
# still running? docs/mpi-support.md asserts it, the whole
# `TimeoutSeconds <= ResumeTimeout - 120` rule rests on it, and the unit suite cannot
# answer it — a stub scontrol has nothing to perform the transition.
#
# Deliberately uses a stub ResumeProgram rather than the real resume.py. What is under test
# is Slurm's timeout behavior, so the stub isolates it: no AWS account, no credentials, and
# no chance of resume.py's own logic explaining the result. It blocks and never registers,
# which is exactly what resume.py looks like to slurmctld during a slow launch.
#
# Usage:  ./run-resume-timeout-experiment.sh [podman|docker]
# Takes about 6 minutes, most of it waiting out a 180s timeout twice over.

set -euo pipefail

ENGINE="${1:-podman}"
HERE="$(cd "$(dirname "$0")" && pwd)"
IMAGE=slurm-resume-timeout-rig

command -v "$ENGINE" >/dev/null || { echo "no $ENGINE on PATH"; exit 2; }

echo "==> Building $IMAGE"
"$ENGINE" build -q -t "$IMAGE" "$HERE" >/dev/null

# $1 case label, $2 slurm.conf, $3 resume script, $4 seconds to observe
run_case() {
    local label="$1" conf="$2" resume="$3" observe="$4"
    local name="rig-$label"

    echo
    echo "=================================================================="
    echo "CASE: $label   ($conf, $resume)"
    echo "=================================================================="

    "$ENGINE" rm -f "$name" >/dev/null 2>&1 || true
    "$ENGINE" run -d --name "$name" "$IMAGE" sleep infinity >/dev/null
    sleep 2

    "$ENGINE" exec "$name" bash -lc "
        set -e
        cp /rig/$conf /etc/slurm/slurm.conf
        cp /rig/$resume /rig/resume_stub.sh
        chmod +x /rig/resume_stub.sh
        runuser -u munge -- /usr/sbin/munged --force 2>/dev/null || true
        sleep 1
        runuser -u slurm -- /usr/sbin/slurmctld
        sleep 6
        pgrep slurmctld >/dev/null || { echo 'slurmctld failed to start'; tail -20 /var/log/slurm/slurmctld.log; exit 1; }

        # Fail loudly rather than reporting a non-result. Slurm disables power save
        # entirely if either program is missing or non-executable, and then nothing ever
        # calls ResumeProgram — which looks identical to 'the timeout never fired'.
        if grep -q 'power_save module disabled' /var/log/slurm/slurmctld.log; then
            echo 'RIG BROKEN: Slurm disabled power save, so ResumeProgram will never run:'
            grep 'power_save' /var/log/slurm/slurmctld.log | tail -3
            exit 1
        fi

        echo '--- initial state (expect IDLE+CLOUD+POWERED_DOWN) ---'
        scontrol show node aws-compute-0 | grep -o 'State=[^ ]*'

        # Trigger the resume path. salloc blocks until the allocation is granted or the
        # nodes fail, so it must run detached with its own output discarded.
        setsid salloc --no-shell -p mpi -N 4 -t 10 >/dev/null 2>&1 < /dev/null &
        sleep 8

        [ -f /rig/resume.log ] || { echo 'RIG BROKEN: ResumeProgram was never invoked'; \
            grep -iE 'power|error' /var/log/slurm/slurmctld.log | tail -5; exit 1; }

        # Track the job alongside the node. The node state is the mechanism, but the job is
        # what the operator actually sees, and 'CONFIGURING for three minutes then gone' is
        # the symptom that sends people to the docs.
        START=\$(date +%s)
        while [ \$(( \$(date +%s) - START )) -lt $observe ]; do
            printf 't+%-5s %-32s %-22s job=%s\n' \
              \"\$(( \$(date +%s) - START ))s\" \
              \"\$(scontrol show node aws-compute-0 | grep -o 'State=[^ ]*')\" \
              \"\$(sinfo -h -o '%E' -n aws-compute-0)\" \
              \"\$(squeue -h -o '%T' -j 1 2>/dev/null || echo gone)\"
            sleep 20
        done

        echo '--- resume program still running? ---'
        # Match the script name exactly. pgrep -f matches this very shell (the string is in
        # its argv) and bare 'sleep' matches the container's own sleep infinity; both false
        # positives previously made a broken rig look like a working one.
        if pgrep -x resume_stub.sh >/dev/null; then
            echo 'YES - still waiting, so Slurm decided while the program was mid-flight'
        else
            echo 'no - it had already finished'
        fi
        echo '--- slurmctld verdict ---'
        grep -E 'waking nodes|not resumed by ResumeTimeout|Killing JobId' /var/log/slurm/slurmctld.log || echo '(no ResumeTimeout line - nodes resumed in time)'
        echo '--- final ---'
        scontrol show node aws-compute-0 | grep -E 'State=|Reason=' || true
    "

    "$ENGINE" rm -f "$name" >/dev/null 2>&1 || true
}

# TimeoutSeconds >= ResumeTimeout: the failure the docs warn about.
run_case timeout-too-long slurm.conf.timeout-too-long resume-blocks-past-timeout.sh 220

# The inverse: enough headroom, nodes register, no timeout. Observed for longer than case
# 1's 180s ResumeTimeout on purpose — stopping at 80s would only show that we quit watching
# before anything could go wrong, which proves nothing.
run_case timeout-ok slurm.conf.timeout-ok resume-registers-in-time.sh 200

echo
echo "=================================================================="
echo "Expected results"
echo "=================================================================="
echo "Case 1 (TimeoutSeconds >= ResumeTimeout): nodes reach"
echo "  DOWN+CLOUD+POWERED_DOWN+NOT_RESPONDING with Reason='ResumeTimeout reached',"
echo "  logged as 'not resumed by ResumeTimeout(180)', while the resume program is"
echo "  STILL RUNNING. Fires between +0.8s and +10.0s past the 180s deadline across runs"
echo "  (Slurm's power-save poll interval) — so treat it as no grace period at all."
echo
echo "Case 2 (ResumeTimeout=600, registers at 40s): no ResumeTimeout line in"
echo "  slurmctld.log even after 200s — past the 180s that killed case 1 — and the job"
echo "  is still CONFIGURING rather than gone. Job survival is the clearest contrast:"
echo "  case 1's job disappears at the deadline, case 2's does not."
echo
echo "Note on case 2's state: nodes stay MIXED+CLOUD+NOT_RESPONDING+POWERING_UP rather"
echo "than reaching 'allocated'. That is expected here and not a failure — 'scontrol"
echo "update nodeaddr=' only tells the controller where a node is; clearing"
echo "NOT_RESPONDING needs a real slurmd to check in, which this rig has none of. What"
echo "case 2 establishes is the absence of the timeout, which is the claim under test."
