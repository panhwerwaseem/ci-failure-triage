# When a re-run is legitimate

A re-run is an experiment, not a fix. It is legitimate when it can tell you
something, and wasteful or harmful when it is used to make a red job go
away.

## The test

Re-run only when all four hold:

1. The first failure has been found and classified.
2. The class is one where a second attempt can end differently with no change
   to the repository or the CI settings.
3. You have said what each outcome will mean before starting it.
4. It is the first re-run for this failure. A second identical failure ends
   the experiment.

## By category

| Category | Re-run? | Reason |
|---|---|---|
| `runner` | Once | A different or restarted runner may be healthy |
| `network` | Once | Outages and resets are often brief |
| `network`, connection refused to a service the job starts | No | The job needs a readiness check. A re-run only sometimes wins the race |
| `cache-artifact`, transfer failure | Once | Storage errors are often brief |
| `cache-artifact`, size limit | No | The archive is as large the second time |
| `rate-limit` | Not immediately | The limit window has to pass, or the pulls have to be authenticated or mirrored |
| `timeout` | Once, if nothing points at the code | A second timeout at the same place is a hang |
| `out-of-memory` | No | The same job needs the same memory. Change the limit or the job first |
| `disk-full` | No, until space is freed | The same runner is still full |
| `credentials` | No | The credential is still wrong |
| `compile`, `type-check`, `lint` | No | The result is deterministic |
| `test` | No | See below |
| `missing-dependency` | Only if an earlier download failed | Then the download failure is the real class |
| `exception`, `generic-error`, `exit-only`, `missing-output` | No | Find the cause first |

## What a green re-run proves

- After an infrastructure failure: the job can pass when the infrastructure
  behaves. Record the failure anyway. If the same category shows up again
  soon, report it to the owner instead of re-running again.
- After a `test` failure with no code change: the test passes sometimes and
  fails sometimes. That is a flaky test, which is a defect to reproduce and
  fix. It is not a resolved failure.
- After a `timeout`: the hang is intermittent. It is still there.

A green re-run never proves that a code change fixed anything unless the
change was made between the two runs and the failing command was also run
locally.

## What is never legitimate

- Re-running until green.
- Re-running before the log has been read.
- Re-running a deterministic failure (compile, type check, lint).
- Clearing caches and re-running without a reason to suspect the cache.
  Clearing a cache is a change. Say why it is being made.
- Adding automatic retries to the pipeline so that failures stop being seen.

## Automatic retries in pipeline configuration

Automatic retries are reasonable for failures the repository cannot cause,
and harmful when they cover script failures, because a retried test failure
is a hidden flaky test.

- Scope a retry to infrastructure failure reasons where the provider lets you
  choose. GitLab CI's `retry` keyword accepts `when:` with failure reasons such
  as `runner_system_failure` and `stuck_or_timeout_failure`. Buildkite's
  `retry.automatic` rules match on exit status, so a rule can target the
  status an agent loss produces without retrying ordinary command failures.
  Check each provider's documentation for the current list of reasons and
  statuses.
- Keep the limit small, one or two.
- Do not add a retry while triaging a failure. Propose it separately and let
  the user decide.
