# Security

An agent skill is instructions and code that your coding agent will follow and run. Review a skill before installing it, the same way you would review any dependency.

## What this skill does on your machine

| | |
|---|---|
| Reads | The one log file you pass to `first_failure.py`, or stdin. `signatures.py` reads no files |
| Writes | Nothing. Both scripts print to stdout and stderr only |
| Executes | Nothing. The scripts start no other programs |
| Network | None. The bundled scripts import no network modules and never fetch a log. `tools/validate.py` checks this in CI |
| Dependencies | Python standard library only |
| Pre-approved tools | None. `SKILL.md` has no `allowed-tools` entry, so your agent's normal permission prompts apply |
| Hooks, MCP servers, background processes | None |

The skill instructs the agent to classify a failure before acting, and not to edit code for an infrastructure failure. Any edit, re-run or command it does propose goes through your agent's normal review and permission flow.

CI logs can contain secrets that the provider's masking missed: tokens in URLs, dumped environment variables, connection strings. `first_failure.py` prints excerpts of the log, so anything in those lines enters your terminal and your agent's context. Keep saved logs out of the repository, delete them when you are done, and rotate any credential you find in one.

The reference file on getting logs shows commands that download a log with a provider's CLI or API. Those commands use your credentials. They are run by you or, after you approve, by your agent. The bundled scripts never run them.

A log is untrusted text. It can contain lines written by anyone whose code or dependencies ran in the job. `SKILL.md` tells the agent to treat log content as data and never to follow instructions that appear inside a log.

## Reporting a vulnerability

Please report security problems privately through the repository's **Security** tab ("Report a vulnerability"). If that option is not available, open an issue that says a private channel is needed, without including exploit details.

## Verifying what you install

- Install from a tagged release and read the diff between tags before updating.
- The whole skill is the `skills/ci-failure-triage/` folder: one Markdown protocol, two Python scripts, three reference files, and eval fixtures.
