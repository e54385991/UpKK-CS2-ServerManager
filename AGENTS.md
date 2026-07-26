# Task Completion Checks

Before reporting any task that changes repository files complete, run the
applicable baseline checks at least once and report the result. For every task
that changes Python code or Python tooling, run the full quality baseline:

```bash
uv run python scripts/check_baseline.py
```

Do not report the task as complete until the check passes. If the check cannot
be run, report the exact command, failure, and remaining risk.
