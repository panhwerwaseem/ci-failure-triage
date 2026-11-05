# ci-failure-triage

**An agent skill that finds the first real failure in a red CI job log and says whether it is infrastructure or code, before anything is re-run or edited.**

Left alone, a coding agent asked to fix a red pipeline tends to read the last error in the log, treat it as the cause, and either edit code until something changes or re-run the job and hope. The last error is often a consequence: a missing module because an install failed earlier, "no test reports found" because the build never compiled. This skill replaces that with a protocol and a script: find the first failure, quote the line, classify it, and only then act. It will not change code to work around a broken runner, and it will not skip the failing step to get a green build.

It is written for GitLab CI, Buildkite, CircleCI and Jenkins logs, and also reads plain-text and GitHub Actions logs.

Works with Claude Code, and with any agent that reads the open [Agent Skills](https://agentskills.io) format (Codex, Cursor, GitHub Copilot, Gemini CLI and others).

## What it looks like

The commands below were run from `skills/ci-failure-triage/` on the bundled fixtures.

A Buildkite build whose last error is "Cannot find module". The first failure is a failed registry download 28 lines earlier:

```console
$ python3 scripts/first_failure.py evals/files/buildkite-install-cascade.log --context 1
log: evals/files/buildkite-install-cascade.log   provider: buildkite   lines: 62
FIRST FAILURE at line 16   class: infrastructure / network   confidence: medium
  step:    :npm: Installing dependencies
  command: ./ci/build.sh   (echoed at line 12)
  what:    dependency download, registry or network request failed
excerpt:
       15 | npm WARN deprecated glob@7.2.3: Glob versions prior to v9 are no longer supported
  >    16 | npm ERR! code ECONNRESET
       17 | npm ERR! syscall read
response: Do not change code. Re-run once. If it repeats, check the registry or service status and the proxy or mirror settings.
later error lines: 5 (code/missing-dependency 3, infrastructure/network 2). First 5, probable cascade:
       18  infrastructure/network  [:npm: Installing dependencies]  npm ERR! errno ECONNRESET
       19  infrastructure/network  [:npm: Installing dependencies]  npm ERR! network request to https://registry.npmjs.org/typescript/-/typescript-5.4.5.tgz failed, reason: read 
       32  code/missing-dependency  [:typescript: Building]  sh: 1: tsc: not found
       38  code/missing-dependency  [:jest: Running tests]  sh: 1: jest: not found
       44  code/missing-dependency  [:mag: Checking bundle size]  Error: Cannot find module 'chalk'
job end marker: line 60  🚨 Error: The command exited with status 1
notes:
  - Errors continue in later step(s): :typescript: Building, :jest: Running tests, :mag: Checking bundle size. Either the job kept running after the first failure, so the later errors are cascade, or the first error was recovered from and the next candidate is the real one. Read both.
  - Later error lines include class 'code' (first at line 32). They come after the first failure, so they are reported as cascade, not as the cause.
```

A Jenkins build that reports "No test report files were found. Configuration error?". The first failure is a compile error in the Build stage:

```console
$ python3 scripts/first_failure.py evals/files/jenkins-compile-error.log --context 2 --max-later 4
log: evals/files/jenkins-compile-error.log   provider: jenkins   lines: 89
FIRST FAILURE at line 41   class: code / compile   confidence: high
  step:    Build
  command: mvn -B -ntp clean verify   (echoed at line 23)
  what:    compiler or parser rejected the source
excerpt:
       39 | [ERROR] COMPILATION ERROR :
       40 | [INFO] -------------------------------------------------------------
  >    41 | [ERROR] /var/jenkins/workspace/orders_main/src/main/java/com/acme/orders/OrderService.java:[42,29] cannot find symbol
       42 |   symbol:   method totalWithTax()
       43 |   location: variable order of type com.acme.orders.Order
response: Fix the source at the reported file and line. A re-run cannot help.
later error lines: 4 (code/compile 1, unknown/generic-error 2, unknown/missing-output 1). First 4, probable cascade:
       53  code/compile  [Build]  [ERROR] /var/jenkins/workspace/orders_main/src/main/java/com/acme/orders/OrderService.java:[42,29] cannot find
       54  unknown/generic-error  [Build]  [ERROR]   symbol:   method totalWithTax()
       55  unknown/generic-error  [Build]  [ERROR]   location: variable order of type com.acme.orders.Order
       76  unknown/missing-output  [Declarative: Post Actions]  hudson.AbortException: No test report files were found. Configuration error?
job end marker: line 89  Finished: FAILURE
```

A job that passed, with test names full of words like "timeout" and "connection reset", and one download retry that recovered:

```console
$ python3 scripts/first_failure.py evals/files/gitlab-passing.log
log: evals/files/gitlab-passing.log   provider: gitlab   lines: 54
NO FAILURE FOUND
success marker: line 54  Job succeeded
non-fatal infrastructure warnings:
       21  network  WARNING: Retrying (Retry(total=4, connect=None, read=None, redirect=None, status=None)) after connection broke
```

## Install

Pick one route. Installing through more than one gives you the skill twice.

**Claude Code (plugin)**

```
/plugin install ci-failure-triage --marketplace panhwerwaseem/ci-failure-triage
```

On Claude Code versions before 2.1.275, use two steps:

```
/plugin marketplace add panhwerwaseem/ci-failure-triage
/plugin install ci-failure-triage@panhwerwaseem-ci-failure-triage
```

**Any agent (skills CLI)**

```bash
npx skills add panhwerwaseem/ci-failure-triage
```

**Manual**

Copy the `skills/ci-failure-triage` folder into `.claude/skills/` (Claude Code) or `.agents/skills/` (cross-agent) in your project, or into the same folder under your home directory.

To pin a release, add the tag: `/plugin marketplace add panhwerwaseem/ci-failure-triage#v0.1.0` or `npx skills add panhwerwaseem/ci-failure-triage@v0.1.0`. Third-party plugins do not update automatically; run `claude plugin update ci-failure-triage@panhwerwaseem-ci-failure-triage` to update.

## Use

Save the failed job's raw log to a file, describe the problem, and the skill loads on its own:

> The pipeline on main is red. The job log is in build.log. What broke, and can I just retry it?

In Claude Code you can also call it directly with `/ci-failure-triage`.

The script does not download logs. `references/getting-logs.md` lists how to save one from each provider.

## What is in the skill

| File | Purpose |
|---|---|
| `SKILL.md` | The protocol: seven hard rules and a six-step workflow from saved log to report |
| `scripts/first_failure.py` | Reads one saved log. Strips colour codes and timestamps, follows step markers, and prints the first failure with its line number, step, last echoed command, class, category, confidence, an excerpt, and the later error lines it treats as cascade |
| `scripts/signatures.py` | The catalogue of failure signatures that `first_failure.py` uses, in one file so it can be audited. Run it to list them, or to see how a single line is classified |
| `references/failure-taxonomy.md` | What each class and category means, how to confirm it, and the right response |
| `references/getting-logs.md` | How to save a raw job log from GitLab CI, Buildkite, CircleCI, Jenkins and GitHub Actions |
| `references/rerun-policy.md` | When a re-run is a legitimate experiment and when it only hides the problem |
| `evals/` | Seven synthetic logs in four providers' styles (five with a planted root cause, two passing) with eval cases, plus trigger queries |

### How the first failure is chosen

1. Every line is cleaned: ANSI escape sequences, leading timestamps and carriage-return overwrites are removed. Line numbers are those of the original file.
2. Step markers set the current step: GitLab `section_start` and `section_end`, Buildkite `---`, `+++` and `~~~` groups, Jenkins `[Pipeline] { (Stage)`, the CircleCI shell preamble, GitHub `##[group]`.
3. Each line is tested against the signatures in order. Passing-test lines, warnings and command echoes are never failures. Exit markers such as `ERROR: Job failed: exit code 1` or `Finished: FAILURE` say that the job stopped, not why, so they are reported separately.
4. The earliest failure line wins. Everything after it is listed as probable cascade.
5. Confidence is lowered, with a note saying why, when errors continue into later steps, when earlier warnings match infrastructure signatures, when test failures follow a line that may only be logged output, or when the line matched generic error wording only.

### What it does not do

- It does not fetch logs, call any CI API, or re-run anything.
- It matches text. It cannot tell an error logged by a passing test from a fatal one, or a download that failed from one that was retried successfully. The notes flag these cases and the protocol has the agent check the excerpt.
- It reads one job per file. It does not follow a pipeline across jobs.
- Output from parallel branches that is interleaved in one log is read in file order.
- The fixtures are synthetic. Real logs vary by provider version and by tool; a line no signature matches is reported as unknown, not guessed.

## Requirements

- Python 3.9 or later
- No packages to install and no network access

## What it reads, writes and contacts

- **Reads:** the one log file you pass to `first_failure.py`, or stdin.
- **Writes:** nothing. Results go to stdout, errors to stderr.
- **Runs:** nothing. The scripts start no other programs.
- **Contacts:** nothing. The scripts contain no network code; CI enforces this.
- **Permissions:** the skill pre-approves no tools. If the agent offers to save a log with a provider's CLI, or to re-run a job, it asks first, as usual.

See [SECURITY.md](SECURITY.md).

## Testing status

- The scripts have unit tests that run in CI on Python 3.9 and 3.12, together with a validator for the skill format and plugin manifests. The tests run `first_failure.py` against every fixture and check that each planted root cause is reported at its known line and that the passing logs produce no failure.
- The eval cases in `skills/ci-failure-triage/evals/` are in the [skill-creator](https://github.com/anthropics/skills/tree/main/skills/skill-creator) format. A with-skill versus without-skill score has not been published yet; results are welcome as an issue or pull request.

## Development

```bash
python3 tools/validate.py              # skill format, manifests, no-network check
python3 -m unittest discover -s tests  # script unit tests
```

The fixture logs contain real escape and carriage-return bytes. View them with `cat -v`.

## License

[MIT](LICENSE)
