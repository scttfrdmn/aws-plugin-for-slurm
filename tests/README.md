# Tests

Standard-library `unittest` only. The plugin has no runtime dependencies beyond
`boto3`, and the tests add none — `boto3` itself is faked, so the suite runs with a
bare Python interpreter and never touches AWS.

```bash
python3 -m unittest discover -s tests -t . -v   # everything
python3 -m unittest tests.test_validate -v      # one module

RUN_MUTATION_TESTS=1 python3 -m unittest tests.test_mutations -v   # slower, opt-in
```

Run from the repository root. `-t .` sets the top-level directory so `tests.harness`
resolves; without it, discovery imports the modules under a different name and the
in-process `import common` breaks.

## How the plugin is tested without AWS or a Slurm controller

`resume.py`, `change_state.py`, and `generate_conf.py` are scripts, not modules: they
call `common.get_common()` and run their work at import time. There is nothing to
import and call, so they are exercised the way Slurm invokes them — as a subprocess.

`harness.py` builds a throwaway sandbox per test:

```
<tmpdir>/
├── resume.py, common.py, ...   copied from the repo
├── boto3.py                    fake, shadows the real boto3 (sys.path[0])
├── config.json                 written by the test
├── partitions.json             written by the test
└── bin/
    ├── scontrol                stub shell script
    └── sinfo                   stub shell script
```

Two properties make this work:

- `python3 <tmpdir>/resume.py` puts `<tmpdir>` first on `sys.path`, so the fake
  `boto3.py` shadows the installed one — no import ordering to get right.
- `common.py` locates `config.json` relative to its own `__file__`, so copying the
  plugin into the sandbox is what redirects it at the test's config.

### The two in-process exceptions

`test_validate.py` and `test_node_names.py` call pure functions in `common.py`, so they
`import common` directly rather than spawning a subprocess. That import pulls in
`boto3` at module scope, which the sandbox trick cannot help with. `tests/__init__.py`
registers `fake_boto3` in `sys.modules` as a fallback when the real package is absent.

This is the one place the suite does touch `sys.modules`, and it exists so results do
not depend on whether the developer happens to have boto3 installed — a bare CI runner
and a laptop with the AWS SDK must agree. `tests/test_no_dependencies.py` pins it by
re-running those modules in a subprocess where importing the real `boto3` is blocked
outright.

### Asserting on behavior

The fake EC2 client appends every call to a JSON journal. A test asserts on what the
plugin *did* — which instances it tagged, which it terminated — rather than on log
text:

```python
sandbox.run('resume.py', 'mpi-compute-[0-3]')
self.assertEqual(sandbox.journal_calls('terminate_instances'), [...])
```

The `scontrol` stub records its argv to a file and can be told to fail for specific
node names, which is how the "registration failed" paths are driven.

### Scenarios

The fake client's behavior comes from a JSON scenario file. `describe_instances`
takes a *list* of responses so an instance can be `pending` on the first poll and
`running` on the second; the last entry repeats once exhausted.

The synchronous launch path sleeps between polls. `FAKE_EC2_NO_SLEEP=1` makes the
fake replace `time.sleep` with a no-op that still advances a fake clock, so timeout
behavior is tested without the wall-clock wait.

## What these tests do not cover

- Anything requiring a live `slurmctld` — a stub `scontrol` has nothing to perform a node
  state transition. That class of question lives in [integration/](integration/), which
  runs a real controller in a container. It is not part of `unittest discover` and does
  not run in CI; run it by hand. The `ResumeTimeout` behavior (issue #12) is measured
  there.
- Real EC2 semantics. The fake returns what the scenario says. It was written against
  observed `CreateFleet`/`DescribeInstances` shapes, but it is not a validator: it will
  not catch a request field that AWS would reject.
- `health_check.py` network probes, and the `network` (ICMP) health check.
