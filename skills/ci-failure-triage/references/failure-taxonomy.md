# Failure taxonomy

What each class and category reported by `scripts/first_failure.py` means, how
to confirm it, and the right response. The script's category names are in
backticks.

## Contents

- [The two classes](#the-two-classes)
- [Infrastructure categories](#infrastructure-categories)
- [Code categories](#code-categories)
- [Unknown categories](#unknown-categories)
- [Common cascades](#common-cascades)
- [Passes locally, fails in CI](#passes-locally-fails-in-ci)

## The two classes

**Infrastructure.** The cause is outside the repository: the runner, its
memory or disk, the network, a registry, a rate limit, a credential, the
provider. No change to source, tests or dependencies fixes it. The fix belongs
to whoever operates the runners, the registry or the CI settings.

**Code.** The cause is in the repository: source, tests, dependency manifests,
lockfiles, or the pipeline definition. A re-run without a change gives the
same result.

**Unknown.** The script found error wording or an exit marker but no known
signature. Read the log before choosing a class.

The class comes from pattern matching. Confirm it with the checks below before
acting on it.

## Infrastructure categories

### `out-of-memory`

**Looks like.** A bare `Killed` line, exit code 137 (128 + 9, the process
received SIGKILL), `JavaScript heap out of memory`,
`java.lang.OutOfMemoryError`, the compiler reporting
`Killed signal terminated program`, a build daemon that disappeared.

**Confirm.** Exit 137 says the process was killed, not who killed it. The
out-of-memory killer is the usual sender, but a cancellation or a hard time
limit can send the same signal. Check the memory limit of the job's container
or runner, the provider's resource usage view for the job if it has one, and
on a runner you control the kernel log.

**Response.** Give the job more memory, run fewer things at once (test
workers, parallel compile jobs), or set the tool's own heap limit below the
container limit so it fails with a clear message. If memory use rose with a
recent commit, that commit is the cause and this is a code regression.

**Re-run.** Rarely useful. The same job on the same size of runner needs the
same memory.

### `disk-full`

**Looks like.** `No space left on device`, `ENOSPC`, `Disk quota exceeded`.
Later lines are often odd: compilers failing to write temporary files,
truncated archives, corrupt caches.

**Confirm.** On a runner you control, check free space and free inodes. The
same error is returned when inodes run out while space remains.

**Response.** Remove old workspaces, container images and build caches on the
runner, or enlarge its disk. On a fresh hosted runner the job itself filled
the disk: reduce what it downloads, builds or caches.

**Re-run.** On the same self-hosted runner it fails again until space is
freed. On a different runner it may pass, which hides the problem on the first
one.

### `runner`

**Looks like.** GitLab: `ERROR: Job failed (system failure)`. Jenkins: the
agent went offline, the remoting channel closed, `Cannot contact <agent>`.
Any provider: the Docker daemon could not be reached, the runner received a
shutdown signal. Exit code 143 (128 + 15, SIGTERM) means the job was told to
stop: a cancellation, or a runner being shut down or reclaimed.

**Confirm.** Look at the runner or agent status in the provider at the time of
the failure. Check whether other jobs on the same runner died at the same
moment.

**Response.** Nothing in the repository. Re-run once. If it repeats, give the
runner's operator the quoted line, the job link and the runner name.

**Re-run.** Yes, once.

### `network`

**Looks like.** DNS failures (`Could not resolve host`,
`Temporary failure in name resolution`), resets and timeouts (`ECONNRESET`,
`ETIMEDOUT`, `Connection timed out`, `TLS handshake timeout`), gateway errors
from a registry (502, 503, 504), a package manager that could not download a
dependency or a container image.

**Confirm.** Check the status page of the registry or service. Look for the
same failure in unrelated jobs at the same time.

**Response.** Re-run once. If it keeps happening, the durable fixes are a
registry mirror or pull-through cache, dependency caching, and the package
manager's own retry setting for the download step. Those are pipeline changes
to propose, not to slip in while triaging.

**Connection refused** has medium confidence because it has two causes. A
remote service being down is infrastructure. A service container (a database,
a browser) that was not ready when the tests started is a pipeline fault: the
job needs a readiness check, and a re-run only sometimes wins the race.

**Re-run.** Yes, once.

### `rate-limit`

**Looks like.** `toomanyrequests` from a container registry, HTTP 429, an API
saying its rate limit was exceeded.

**Confirm.** The message normally names the limit. Check whether the pulls or
API calls were authenticated; anonymous limits are much lower.

**Response.** Wait for the limit window to pass, authenticate the pulls, or
use a mirror or proxy cache for the registry.

**Re-run.** Not immediately. It will hit the same limit.

### `credentials`

**Looks like.** `401 Unauthorized`, `403 Forbidden`, `Authentication failed`,
`Permission denied (publickey)`, `requested access to the resource is denied`,
`Unable to locate credentials`.

**Confirm.** Find which secret the step uses. Check that it exists for this
branch and pipeline type, and that it has not expired or been rotated. Jobs
from forks and from unprotected branches often do not receive protected
variables or secrets at all; check that first.

**Response.** Fix the secret or the permission in the CI settings. Never put a
credential in the repository or print one into the log to debug.

**Re-run.** No. The credential is as wrong on the second attempt.

### `cache-artifact`

**Looks like.** A failed upload or download of a cache or artifact, an archive
that is too large.

**Confirm.** Many providers treat cache problems as warnings and carry on, so
check that this line actually ended the job. An upload that was refused for
size is a limit, not an outage.

**Response.** Re-run once for a transfer failure. For a size limit, reduce
what is archived or have the limit raised.

**Re-run.** Yes, once, for transfer failures. No, for size limits.

### `timeout`

**Looks like.** CircleCI: `Too long with no output`. GitLab:
`execution took longer than`. Jenkins: `Build timed out`,
`Cancelling nested steps due to timeout`.

**Confirm.** A time limit tells you where the job stopped, not why. Find the
last line of output before the silence and compare the job's duration with
the last green run. A job that is always close to the limit is slow. A job
that normally finishes quickly and then goes silent is hung.

**Response.** For a hang, find what was running: a test waiting on something
that never happens, a prompt waiting for input, a deadlock, a download with no
timeout of its own. Raise the limit only when the job has a real reason to
take longer now.

**Re-run.** Once, and only if nothing in the excerpt points at the code. A
second timeout at the same place is a hang to debug.

## Code categories

### `compile` and `type-check`

The compiler, parser or type checker rejected the source and printed a file
and position. Fix the source there. Nothing else helps. If it compiles
locally, see [Passes locally, fails in CI](#passes-locally-fails-in-ci).

### `lint`

A linter or formatter check failed. Fix the reported lines, or run the
formatter and commit the result. Do not disable the rule, lower the severity,
or remove the step to get past it.

### `test`

A test failed or an assertion did not hold. Reproduce it locally with the
command the script printed. Then decide from the evidence whether the product
or the test is wrong. A test that fails only sometimes is still in this
category: it is a defect in the test or the product, and re-running until
green hides it.

### `missing-dependency`

A module, package, version or command-line tool was not found. Three causes:

- The dependency is not declared, or the lockfile does not match the manifest.
  Fix the manifest and lockfile.
- The tool is not in the job's image, or the install step is missing from the
  pipeline. Fix the pipeline.
- The install or download failed earlier and the job carried on. Then this
  line is cascade and the earlier failure is the one to classify.

Rule out the third before touching a manifest. The script lowers confidence
when earlier warnings match infrastructure signatures.

### `exception`

An uncaught exception or panic. Read the stack trace, reproduce and fix. If
the exception only wraps a network or resource error, classify by that error.

## Unknown categories

### `missing-output`

A step found nothing to collect: no test reports, no artifacts matching the
pattern, no files to upload. Usually an earlier step failed before producing
them. Look above the line. Change the collection pattern only when the build
succeeded and wrote its files somewhere else.

### `generic-error`

The line has error wording but no known signature. Read the excerpt and name
the cause before acting.

### `exit-only`

Only an exit marker was found, such as a non-zero exit code. The cause is in
the output above the marker. Re-run the script with a larger `--context`.

## Common cascades

| First failure | What the later errors look like |
|---|---|
| Dependency install or download failed | Command not found, module not found, in later steps |
| Compile failed | No test reports found, no artifacts to archive |
| Disk full | Compilers and archivers failing in unrelated ways, corrupt caches |
| Out of memory | `Killed`, exit 137, a daemon that vanished, connections refused by the killed process |
| Service container did not start | Connection refused in every test that uses it |
| Checkout or credential failure | Missing files, missing submodules, permission errors later |

## Passes locally, fails in CI

Check these differences before assuming CI is broken:

- CI starts from a clean checkout. A file that exists on your machine but is
  not committed (generated code, a local config, an ignored file) is missing
  there.
- CI installs from the lockfile. A dependency installed locally but never
  declared is missing there.
- Tool and language versions differ. Compare the versions the job printed with
  yours.
- File names are case-sensitive on Linux runners. An import whose case does
  not match the file name works on macOS and Windows and fails on Linux.
- Environment variables, locale and timezone differ.
- CI may run tests in parallel or in a different order.
