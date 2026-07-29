# Integration rig: a real slurmctld

The unit suite in `tests/` covers what the plugin does. It cannot cover **how Slurm reacts
to what the plugin does**, because its `scontrol` is a stub and a stub cannot perform a node
state transition. This directory holds a containerized `slurmctld` for that one class of
question.

It is not part of `python -m unittest discover` and it does not run in CI. It needs a
container engine and takes about seven minutes, most of that spent deliberately waiting out
timeouts. Run it by hand when you change something that depends on Slurm's own behavior.

```bash
cd tests/integration
./run-resume-timeout-experiment.sh podman     # or docker
```

## What it answers

The `ResumeTimeout` claim in [docs/mpi-support.md](../../docs/mpi-support.md#timeoutseconds-must-be-less-than-resumetimeout)
— that Slurm marks cloud nodes `DOWN` when `ResumeTimeout` expires **while `ResumeProgram`
is still running**. The whole `TimeoutSeconds <= ResumeTimeout - 120` rule rests on it, and
before this rig it was asserted from reading Slurm's source and docs rather than measured.
This was issue #12.

Two cases, deliberately inverse:

| Case | `slurm.conf` | `ResumeProgram` | Expected |
|---|---|---|---|
| `timeout-too-long` | `ResumeTimeout=180` | blocks 300s, never registers | nodes `DOWN`, `Reason=ResumeTimeout reached`, resume program still running |
| `timeout-ok` | `ResumeTimeout=600` | waits 40s, then registers each node | no `ResumeTimeout` line in the log at all |

The second case exists so the first proves something. A rig that only ever produces the
failure has not shown the failure is caused by the timeout — it may just be broken.

Run it more than once if you care about the timing. The delay between `ResumeTimeout`
expiring and the nodes going `DOWN` varied from 0.8s to 10.0s across four runs, because
Slurm notices on a periodic power-save poll. A single run reads like a constant offset and
invites treating it as slack; it is not slack.

## Why stub scripts instead of the real `resume.py`

What is under test is Slurm's timeout behavior, not the plugin's. A stub isolates it: no AWS
account, no credentials, no chance that resume.py's own logic explains the result. To
slurmctld a blocking stub is indistinguishable from resume.py during a slow launch — a
`ResumeProgram` that has not returned and whose nodes have not checked in.

## Reading the output

Case 2's nodes end at `MIXED+CLOUD+NOT_RESPONDING+POWERING_UP`, not `allocated`. That is
expected. `scontrol update nodeaddr=` only tells the controller where a node is; clearing
`NOT_RESPONDING` needs a real slurmd to check in, and this rig runs none. The claim case 2
tests is the *absence* of the timeout.

## Guards

An earlier version of this script appeared to work while proving nothing, so it now fails
loudly rather than reporting a non-result:

- **`power_save module disabled`** in the log → exit 1. Slurm disables power save entirely
  if either program is missing or non-executable, and then `ResumeProgram` is never called —
  which looks exactly like "the timeout never fired."
- **No `/rig/resume.log`** → exit 1. Proof the resume program actually ran.
- **`pgrep -x resume_stub.sh`**, not `pgrep -f`. `-f` matched the checking shell itself,
  because the pattern appears in its own argv.

If you extend this rig, keep that property: every check should be able to fail.

## Files

| File | Purpose |
|---|---|
| `Containerfile` | Rocky 9 + Slurm 22.05.9 from EPEL. No source build |
| `run-resume-timeout-experiment.sh` | Driver. Both cases, guards, verdict |
| `slurm.conf.timeout-too-long` | `ResumeTimeout=180` |
| `slurm.conf.timeout-ok` | `ResumeTimeout=600` |
| `resume-blocks-past-timeout.sh` | Blocks 300s, never registers |
| `resume-registers-in-time.sh` | Waits 40s, then `scontrol update nodeaddr=` per node |
| `suspend_stub.sh` | No-op. Power save needs both programs to exist and be executable |

Slurm 22.05.9 is what EPEL 9 ships. The plugin targets roughly 20.02 through 23.x, and
`ResumeTimeout` semantics have not changed across that range — but if you are chasing a
version-specific question, pin the version in the `Containerfile` and say so in the finding.
