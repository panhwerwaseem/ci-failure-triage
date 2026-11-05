# Changelog

All notable changes to this skill are recorded here. Versions follow [Semantic Versioning](https://semver.org).

## 0.1.0 - 2026-11-06

### Added

- `SKILL.md` with the triage protocol: hard rules and a six-step workflow.
- `scripts/first_failure.py` to find and classify the first failure in a saved job log from GitLab CI, Buildkite, CircleCI or Jenkins.
- `scripts/signatures.py` with the catalogue of failure signatures and a command to inspect it.
- References for the failure taxonomy, for saving logs from each provider, and for when a re-run is legitimate.
- Seven synthetic log fixtures with eval cases, and trigger queries.
- Unit tests, a format validator, and a CI workflow.
