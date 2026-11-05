# Getting a job log

`scripts/first_failure.py` reads a saved log file or stdin. It never fetches a
log. This file lists ways to save one. Fetching uses the user's credentials,
so ask before running any of these commands.

CLI flags and UI labels change between versions. Where this file is not
specific, check the tool's `--help` or the provider's documentation instead of
guessing.

## Contents

- [What to save](#what-to-save)
- [GitLab CI](#gitlab-ci)
- [Buildkite](#buildkite)
- [CircleCI](#circleci)
- [Jenkins](#jenkins)
- [GitHub Actions](#github-actions)
- [Reading from a pipe](#reading-from-a-pipe)

## What to save

- The whole log of the failed job, from its first line. Not the tail, and not
  a screenshot.
- The raw log, not text copied from the rendered web page. The raw log keeps
  the step markers the script uses.
- One job per file. If several jobs failed, save the earliest failed job in
  the earliest stage first.
- Logs can contain secrets that masking missed. Keep saved logs out of the
  repository and delete them when done.

## GitLab CI

- **UI.** On the job page, use the control above the log that shows the
  complete raw log. It opens the job URL with `/raw` appended. Save that page.
- **API.** The job log endpoint is `GET /projects/:id/jobs/:job_id/trace`:

  ```bash
  curl --header "PRIVATE-TOKEN: <token>" \
    "https://gitlab.example.com/api/v4/projects/<project id>/jobs/<job id>/trace" > job.log
  ```

- **CLI.** `glab ci trace <job id> > job.log` prints a job's log. Check
  `glab ci trace --help` for how your version selects the job.

The raw log contains `section_start` and `section_end` markers. The script
reads them to name the step (`step_script`, `get_sources` and so on). GitLab
cuts logs that exceed the instance's size limit and says so in the log; the
script reports that line.

## Buildkite

- **UI.** The job's log panel has a download option.
- **API.** The job log endpoint returns plain text when asked for it:

  ```bash
  curl -H "Authorization: Bearer <token>" -H "Accept: text/plain" \
    "https://api.buildkite.com/v2/organizations/<org>/pipelines/<pipeline>/builds/<build number>/jobs/<job id>/log" > job.log
  ```

  The token must be allowed to read build logs.
- **CLI.** If you use the `bk` CLI, check `bk --help` for a log command in
  your version.

The script reads Buildkite's group headers (lines that start with `---`,
`+++` or `~~~`) as step names.

## CircleCI

- **UI.** Open the failed job and expand the failed step. The step's output
  panel has controls to open the raw output or download it. Each step is a
  separate file. Save the failed step, and the steps before it if the cause
  may be earlier.
- **API and CLI.** Step output is reachable through CircleCI's API. The
  details differ between API versions, so follow the current API
  documentation.

A step's output has no step name in it. When the file starts with the shell
preamble CircleCI shows above a run step (`#!/bin/bash -eo pipefail` followed
by the command), the script uses the command as the step name. If you join
several steps into one file, keep them in order.

## Jenkins

- **URL.** Append `consoleText` to the build URL for the plain-text log:

  ```bash
  curl -u "<user>:<api token>" \
    "https://jenkins.example.com/job/<job name>/<build number>/consoleText" > build.log
  ```

- **UI.** On the build page, open the console output and choose the plain-text
  view.

For Pipeline jobs the log contains `[Pipeline]` lines. The script reads stage
names from them. Freestyle job logs have no stage markers, so no step is
reported. Output from parallel branches is interleaved in one log.

## GitHub Actions

The script also recognises GitHub Actions logs, although the skill is aimed at
the providers above.

```bash
gh run view <run id> --log-failed > job.log
```

`--log` prints every step instead of only the failed ones.

## Reading from a pipe

Any command that prints a log can feed the script directly:

```bash
glab ci trace <job id> | python3 scripts/first_failure.py -
```

Save to a file instead when you will need to quote line numbers later.
