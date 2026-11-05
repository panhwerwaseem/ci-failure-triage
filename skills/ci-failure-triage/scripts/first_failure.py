#!/usr/bin/env python3
"""Find the first real failure in a saved CI job log and classify it.

Reads one saved job log (GitLab CI, Buildkite, CircleCI, Jenkins, or plain
text). Strips ANSI codes and timestamps, tracks step or section markers where
the provider prints them, and reports the earliest line that matches a failure
signature, not the last one. Later error lines are listed as probable cascade.
The first failure is classified as infrastructure (the cause is outside the
repository: memory, disk, runner, network, registry, rate limit, credentials,
cache, time limit) or code (compile, type check, lint, test, missing
dependency, uncaught exception), with a confidence level and the reasons.

This is pattern matching on text. It is evidence for a diagnosis, not the
diagnosis: read the excerpt before acting on the class. The signatures are in
signatures.py, next to this file.

Examples:
  first_failure.py job.log
  first_failure.py job.log --context 20 --max-later 15
  first_failure.py build.log --provider jenkins --json
  some-command-that-prints-a-log | first_failure.py -

Exit codes: 0 = no failure found in the log, 1 = a failure was found,
2 = usage or input error.
Standard library only. Reads the one file it is given (or stdin). Never
fetches logs, makes no network calls, and writes only to stdout and stderr.
"""
import argparse
import json
import os
import re
import sys
from collections import namedtuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from signatures import EXIT_ONLY, GENERIC, RESPONSES, classify_line  # noqa: E402

PROVIDERS = ("auto", "gitlab", "buildkite", "circleci", "jenkins", "github", "generic")
LEVELS = ("low", "medium", "high")
MAX_SHOWN_CHARS = 200
ADOPT_WINDOW = 10   # lines below a generic error or exception searched for a better signature

ANSI_RE = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"        # OSC, ended by BEL or ST
    r"|\x1b[_P^X][^\x07\x1b]*(?:\x07|\x1b\\)"   # APC, DCS, PM, SOS (Buildkite timestamps are APC)
    r"|\x1b\[[0-?]*[ -/]*[@-~]"                 # CSI: colours, cursor moves, erase line
    r"|\x1b[@-Z\\-_]"                           # other two-character escapes
)
TIMESTAMP_RE = re.compile(
    r"^(?:\[?\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:[.,]\d+)?(?:Z|[+-]\d\d:?\d\d)?\]?"
    r"(?: \d\d[OE][ +])?"                       # GitLab stream marker after its timestamp
    r" ?|\[?\d\d:\d\d:\d\d(?:[.,]\d+)?\]?(?=\s|$) {0,2})"   # clock time, e.g. Jenkins Timestamper
)
GH_PREFIX_RE = re.compile(r"^[^\t]*\t([^\t]*)\t(?=\d{4}-\d\d-\d\dT\d\d:)")  # "gh run view --log"
GITLAB_SECTION_RE = re.compile(r"section_(start|end):\d+:([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?")
GITLAB_STAGE_RE = re.compile(r'^Executing "([A-Za-z0-9_.-]+)" stage of the job script')
BUILDKITE_GROUP_RE = re.compile(r"^(?:---|\+\+\+|~~~) (?!(?:FAIL|PASS|SKIP):|[ab]/)(\S.*)$")
JENKINS_STAGE_RE = re.compile(r"^\[Pipeline\] \{ \((.+)\)\s*$")
JENKINS_STAGE_END_RE = re.compile(r"^\[Pipeline\] // stage\b")
CIRCLECI_PREAMBLE_RE = re.compile(r"^#!/bin/(?:ba)?sh\b")
GITHUB_GROUP_RE = re.compile(r"^##\[group\](.+)$")
COMMAND_ECHO_RE = re.compile(r"^(?:\$|[+]+) (\S.*)$")   # "$ cmd" echo, or "+ cmd" from set -x
# Steps that run only after the job has already failed.
POST_STEP_RE = re.compile(
    r"^(?:after_script|upload_artifacts_on_failure|archive_cache_on_failure|"
    r"cleanup_file_variables|Declarative: Post Actions)$|pre-exit hook|post-command hook")
SUCCESS_RE = re.compile(r"^Job succeeded\b|^Finished: SUCCESS\b")
TRUNCATED_RE = re.compile(r"log exceeded limit of \d+ bytes")

Line = namedtuple("Line", "number text step command scan")
Hit = namedtuple("Hit", "line sig")


def detect_provider(text):
    """Guess the provider from markers in the ANSI-stripped log text."""
    if "section_start:" in text or "Running with gitlab-runner" in text:
        return "gitlab"
    if "[Pipeline]" in text or re.search(r"(?m)^(?:\[[^\]]+\] )?Finished: [A-Z_]+\s*$", text):
        return "jenkins"
    if re.search(r"(?m)^~~~ ", text) or "buildkite-agent" in text:
        return "buildkite"
    if "##[group]" in text or "##[error]" in text:
        return "github"
    if re.search(r"(?m)^#!/bin/(?:ba)?sh -eo pipefail|Exited with code exit status \d"
                 r"|Too long with no output|CircleCI received exit code", text):
        return "circleci"
    return "generic"


def parse(text, provider="auto"):
    """Clean every line and attach the step and the last echoed command to it.

    Returns (list of Line, provider). Line numbers are 1-based positions in the
    original file, counted by newline characters. Lines with scan=False are step
    markers or command echoes and are never treated as failures.
    """
    raw_lines = text.replace("\ufeff", "").split("\n")
    if raw_lines and raw_lines[-1] == "":
        raw_lines.pop()
    # Buildkite marks each line with an APC timestamp; note it before it is stripped.
    has_bk_timestamps = "\x1b_bk;" in text
    stripped = [ANSI_RE.sub("", raw) for raw in raw_lines]
    if provider == "auto":
        provider = "buildkite" if has_bk_timestamps else detect_provider("\n".join(stripped))
    has_sections = provider == "gitlab" and any("section_start:" in s for s in stripped)

    lines = []
    step = None
    command = None
    circleci_pending = False
    for number, raw in enumerate(stripped, 1):
        scan = True
        previous_step = step
        if provider == "gitlab":
            for kind, name in GITLAB_SECTION_RE.findall(raw):
                step = name if kind == "start" else None
            raw = GITLAB_SECTION_RE.sub("", raw)
        # A carriage return overwrites the line: keep what a terminal would show last.
        segments = [seg for seg in raw.split("\r") if seg.strip()]
        cleaned = segments[-1] if segments else ""
        gh_prefix = GH_PREFIX_RE.match(cleaned)
        if gh_prefix:
            cleaned = cleaned[gh_prefix.end():]
            if provider == "github":
                step = gh_prefix.group(1) or None
        cleaned = TIMESTAMP_RE.sub("", cleaned, 1).rstrip()

        if provider == "gitlab" and not has_sections:
            match = GITLAB_STAGE_RE.match(cleaned)
            if match:
                step = match.group(1)
        elif provider == "buildkite":
            match = BUILDKITE_GROUP_RE.match(cleaned)
            if match:
                step, scan = match.group(1).strip(), False
            elif cleaned.startswith("^^^ +++"):
                scan = False
        elif provider == "jenkins":
            match = JENKINS_STAGE_RE.match(cleaned)
            if match:
                step = match.group(1)
            elif JENKINS_STAGE_END_RE.match(cleaned):
                step = None
        elif provider == "circleci":
            if CIRCLECI_PREAMBLE_RE.match(cleaned):
                circleci_pending, scan = True, False
            elif circleci_pending and cleaned.strip():
                step = cleaned.strip()
                command = (number, step)
                circleci_pending, scan = False, False
        elif provider == "github":
            match = GITHUB_GROUP_RE.match(cleaned)
            if match:
                scan = False
                if not gh_prefix:
                    step = match.group(1).strip()

        if step and step != previous_step and provider in ("gitlab", "jenkins", "github"):
            command = None   # a new step starts with no command echoed yet
        echo = COMMAND_ECHO_RE.match(cleaned) if scan else None
        if echo:
            command, scan = (number, echo.group(1)), False
        lines.append(Line(number, cleaned, step, command, scan))
    return lines, provider


def _cap(level, ceiling):
    return LEVELS[min(LEVELS.index(level), LEVELS.index(ceiling))]


def _entry(hit):
    return {"line": hit.line.number, "class": hit.sig.klass, "category": hit.sig.category,
            "step": hit.line.step, "text": hit.line.text[:MAX_SHOWN_CHARS]}


def collect(lines):
    """Return (errors, warnings, ends, success line, truncation line) in log order."""
    errors, warnings, ends = [], [], []
    truncated = None
    # A success marker counts only as the last line of the log, where providers print it.
    last = next((line for line in reversed(lines) if line.text.strip()), None)
    success = last if last is not None and SUCCESS_RE.search(last.text) else None
    seen = {}   # logs repeat lines; classify each distinct text once
    for line in lines:
        if not line.scan:
            continue
        if TRUNCATED_RE.search(line.text):
            truncated = line
        if line.text not in seen:
            seen[line.text] = classify_line(line.text)
        kind, sig = seen[line.text]
        if kind == "error":
            errors.append(Hit(line, sig))
        elif kind == "warning":
            warnings.append(Hit(line, sig))
        elif kind == "end":
            ends.append(line)
    return errors, warnings, ends, success, truncated


def analyse(text, provider="auto", context=5, max_later=5):
    """Analyse log text and return the result as a JSON-serialisable dict."""
    lines, provider = parse(text, provider)
    errors, warnings, ends, success, truncated = collect(lines)
    notes = []
    result = {"provider": provider, "lines": len(lines), "verdict": "no-failure",
              "first_failure": None, "later_errors": {"count": 0, "by_category": {}, "shown": []},
              "job_end": None, "success_marker": None,
              "infrastructure_warnings": [_entry(w) for w in warnings[:max_later]], "notes": notes}
    if "\x00" in text:
        notes.append("The file contains NUL bytes. It may be binary or in an unexpected encoding, "
                     "so matches may have been missed.")
    if truncated:
        notes.append("Line %d says the provider cut the log off. The first failure may be "
                     "missing from this file." % truncated.number)
    if ends:
        result["job_end"] = {"line": ends[-1].number, "text": ends[-1].text[:MAX_SHOWN_CHARS]}
    if success:
        result["success_marker"] = {"line": success.number, "text": success.text[:MAX_SHOWN_CHARS]}
        if errors:
            notes.append("The log ends with a success marker, so the %d error-like line(s) in it "
                         "were not fatal (first at line %d)." % (len(errors), errors[0].line.number))
        return result
    if not errors and not ends:
        notes.append("No failure signature, exit marker or success marker found. If the job did "
                     "fail, this file may not hold the whole log.")
        return result

    result["verdict"] = "failure"
    primary = errors[0] if errors else Hit(ends[0], EXIT_ONLY)
    later = errors[1:]
    sig = primary.sig
    confidence = sig.confidence
    # A generic error line or an exception is often only the wrapper. Take the class from a
    # known signature just below it in the same step: an infrastructure one if there is any
    # (a stack trace that wraps a network or resource error), else the nearest, for generic lines.
    adopted = None
    if sig is GENERIC or sig.category == "exception":
        window = [h for h in later if h.line.number - primary.line.number <= ADOPT_WINDOW
                  and h.line.step == primary.line.step and h.sig is not GENERIC]
        infra = [h for h in window if h.sig.klass == "infrastructure"]
        if infra:
            adopted = infra[0]
        elif sig is GENERIC and window:
            adopted = window[0]
        if adopted:
            notes.append("Line %d is %s. The class comes from line %d, a known signature within "
                         "the next %d lines. Read both lines." % (
                             primary.line.number,
                             "an exception" if sig.category == "exception" else "generic error wording",
                             adopted.line.number, ADOPT_WINDOW))
            sig = adopted.sig
            confidence = _cap(sig.confidence, "medium")
        elif sig is GENERIC:
            notes.append("The line matched generic error wording only, not a known signature. "
                         "Read the excerpt before acting.")
    if sig is EXIT_ONLY:
        notes.append("No error line was recognised before the exit marker. The cause is in the "
                     "output above it; widen the excerpt with --context.")
    if sig.category == "timeout":
        notes.append("A time limit shows where the job stopped, not why. The last output before "
                     "the silence is in the excerpt.")

    early_warnings = [w for w in warnings if w.line.number < primary.line.number]
    if early_warnings and sig.klass != "infrastructure":
        confidence = _cap(confidence, "medium")
        first = early_warnings[0].line
        notes.append("%d earlier warning line(s) match infrastructure signatures, first at line "
                     "%d: %s. Check whether this failure follows from them." % (
                         len(early_warnings), first.number, first.text[:120]))
    later_steps = []
    for hit in later:
        step = hit.line.step
        if step and step != primary.line.step and step not in later_steps \
                and not POST_STEP_RE.search(step):
            later_steps.append(step)
    if later_steps:
        confidence = _cap(confidence, "medium")
        notes.append("Errors continue in later step(s): %s. Either the job kept running after the "
                     "first failure, so the later errors are cascade, or the first error was "
                     "recovered from and the next candidate is the real one. Read both." % (
                         ", ".join(later_steps[:4])))
    tests_below = [h for h in later if h.sig.category == "test" and h.line.step == primary.line.step]
    if sig.category != "test" and tests_below:
        confidence = _cap(confidence, "medium")
        notes.append("Test failures are reported below in the same step (first at line %d). If "
                     "line %d is output logged by a passing test, that test failure is the first "
                     "real failure." % (tests_below[0].line.number, primary.line.number))
    other = [h for h in later if h.sig.klass not in (sig.klass, "unknown")]
    if other:
        notes.append("Later error lines include class '%s' (first at line %d). They come after "
                     "the first failure, so they are reported as cascade, not as the cause." % (
                         other[0].sig.klass, other[0].line.number))
    if not any(line.step for line in lines):
        notes.append("No step markers recognised for provider '%s', so no step is reported." % provider)

    index = primary.line.number - 1
    command = primary.line.command
    # For a line outside every step, name the last step that ran before it.
    previous_step = None
    if not primary.line.step:
        previous_step = next((l.step for l in reversed(lines[:index]) if l.step), None)
    result["first_failure"] = {
        "line": primary.line.number,
        "text": primary.line.text[:MAX_SHOWN_CHARS],
        "step": primary.line.step,
        "previous_step": previous_step,
        "command": {"line": command[0], "text": command[1][:MAX_SHOWN_CHARS]} if command else None,
        "class": sig.klass,
        "category": sig.category,
        "what": sig.what,
        "confidence": confidence,
        "class_from_line": adopted.line.number if adopted else None,
        "response": RESPONSES[sig.category],
        "excerpt": [{"line": l.number, "text": l.text[:MAX_SHOWN_CHARS]}
                    for l in lines[max(0, index - context): index + context + 1]],
    }
    by_category = {}
    for hit in later:
        key = "%s/%s" % (hit.sig.klass, hit.sig.category)
        by_category[key] = by_category.get(key, 0) + 1
    result["later_errors"] = {"count": len(later), "by_category": by_category,
                              "shown": [_entry(h) for h in later[:max_later]]}
    return result


def print_report(result, source):
    print("log: %s   provider: %s   lines: %d" % (source, result["provider"], result["lines"]))
    first = result["first_failure"]
    if first is None:
        print("NO FAILURE FOUND")
    else:
        print("FIRST FAILURE at line %d   class: %s / %s   confidence: %s" % (
            first["line"], first["class"], first["category"], first["confidence"]))
        if first["step"]:
            print("  step:    %s" % first["step"])
        elif first["previous_step"]:
            print("  step:    (outside any step; the last step before it was %s)" % first["previous_step"])
        else:
            print("  step:    (none recognised)")
        if first["command"]:
            print("  command: %s   (echoed at line %d)" % (
                first["command"]["text"], first["command"]["line"]))
        print("  what:    %s" % first["what"])
        print("excerpt:")
        for row in first["excerpt"]:
            print("  %s %5d | %s" % (
                ">" if row["line"] == first["line"] else " ", row["line"], row["text"]))
        print("response: %s" % first["response"])
        later = result["later_errors"]
        if later["count"]:
            counts = ", ".join("%s %d" % item for item in sorted(later["by_category"].items()))
            print("later error lines: %d (%s). First %d, probable cascade:" % (
                later["count"], counts, len(later["shown"])))
            for row in later["shown"]:
                print("    %5d  %s/%s  [%s]  %s" % (
                    row["line"], row["class"], row["category"], row["step"] or "-",
                    row["text"][:110]))
    if result["job_end"]:
        print("job end marker: line %d  %s" % (
            result["job_end"]["line"], result["job_end"]["text"][:120]))
    if result["success_marker"]:
        print("success marker: line %d  %s" % (
            result["success_marker"]["line"], result["success_marker"]["text"][:120]))
    if first is None and result["infrastructure_warnings"]:
        print("non-fatal infrastructure warnings:")
        for row in result["infrastructure_warnings"]:
            print("    %5d  %s  %s" % (row["line"], row["category"], row["text"][:110]))
    if result["notes"]:
        print("notes:")
        for note in result["notes"]:
            print("  - " + note)


def read_log(path):
    if path == "-":
        data = sys.stdin.buffer.read()
    else:
        with open(path, "rb") as handle:
            data = handle.read()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):   # UTF-16, e.g. saved with PowerShell redirection
        return data.decode("utf-16", errors="replace")
    return data.decode("utf-8-sig", errors="replace")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log", help="path to a saved job log, or - to read stdin")
    parser.add_argument("--provider", choices=PROVIDERS, default="auto",
                        help="log style; auto detects it from markers in the log (default)")
    parser.add_argument("--context", type=int, default=5, metavar="N",
                        help="lines of excerpt before and after the first failure (default 5)")
    parser.add_argument("--max-later", type=int, default=5, metavar="N",
                        help="how many later error lines to list (default 5)")
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = parser.parse_args(argv)
    if args.context < 0 or args.max_later < 0:
        parser.error("--context and --max-later must not be negative")

    try:
        text = read_log(args.log)
    except (IOError, OSError) as exc:
        print("first_failure.py: cannot read log: %s" % exc, file=sys.stderr)
        return 2
    if not text.strip():
        print("first_failure.py: the log is empty: %s" % args.log, file=sys.stderr)
        return 2

    result = analyse(text, args.provider, args.context, args.max_later)
    result["log"] = args.log
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print_report(result, args.log)
    return 1 if result["verdict"] == "failure" else 0


if __name__ == "__main__":
    sys.exit(main())
