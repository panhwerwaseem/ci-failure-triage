#!/usr/bin/env python3
"""The failure signatures used by first_failure.py, and a tool to inspect them.

A signature is a regular expression with a class (infrastructure, code or
unknown), a category, a base confidence and a one-line meaning. This file is
the whole catalogue, so it can be audited in one place. first_failure.py
imports it; nothing here reads files.

Examples:
  signatures.py                                    # list every signature
  signatures.py --json                             # the same, with the regular expressions
  signatures.py --line 'npm ERR! code ECONNRESET'  # how one cleaned log line is classified

Pass --line the text as first_failure.py prints it (timestamps and colour
codes already removed).

Exit codes: 0 = catalogue listed, or the line is not a failure line,
1 = the line matches a failure signature, 2 = usage error.
Standard library only. Reads no files, makes no network calls, writes only to stdout.
"""
import argparse
import json
import re
import sys
from collections import namedtuple

MAX_MATCH_CHARS = 4000   # only the start of a very long line is matched

Signature = namedtuple("Signature", "klass category confidence what regex")


def _sig(klass, category, confidence, what, pattern):
    return Signature(klass, category, confidence, what, re.compile(pattern))


# "exit code 137", "Exited with code exit status 137", "exited with status 137", "exit code: 137"
_EXIT = r"(?:\b[Ee]xit(?:ed)?(?: with)? (?:code|status):? |non-zero code: )"

# Checked in order. The first signature that matches a line classifies it.
SIGNATURES = [
    # Assertion text wins over any infrastructure word quoted inside it.
    _sig("code", "test", "high", "test assertion failed",
         r"AssertionError|^E\s+assert\s|\bassertion failed\b|expect\(received\)"),
    # Resource exhaustion next: compilers report it with a source location in front.
    _sig("infrastructure", "disk-full", "high", "runner disk or quota is full",
         r"(?i)no space left on device|\bENOSPC\b|disk quota exceeded|not enough space on the disk"),
    _sig("infrastructure", "out-of-memory", "high", "process ran out of memory",
         r"(?i)out of memory|\bOOM-?killed\b|oom-kill|Killed signal terminated program"
         r"|cannot allocate memory|\bENOMEM\b|OutOfMemoryError|^MemoryError\b|Reached heap limit"
         r"|std::bad_alloc|build daemon disappeared unexpectedly"),
    _sig("infrastructure", "out-of-memory", "medium",
         "process was killed with SIGKILL (exit 137), usually by the out-of-memory killer",
         r"(?:^|\s)\d+ Killed\b|^Killed$|Received \"?killed\"? signal|" + _EXIT + r"137\b"),
    # Located compiler and linter diagnostics, so a network word or an error code such as
    # E501 quoted in a diagnostic does not change its class.
    _sig("code", "type-check", "high", "type checker reported an error",
         r"error TS\d+: |^\S+\.pyi?:\d+(?::\d+)?: error: |\.py:\d+:\d+ - error: "),
    _sig("code", "compile", "high", "compiler or parser rejected the source",
         r"^\S+:\d+:\d+: (?:fatal )?error: |\.(?:java|kt|scala|groovy|swift|cs):\d+: error: "
         r"|\.java:\[\d+,\d+\] |^e: \S+\.kts?:"
         r"|^\S+\.go:\d+:\d+: |^error\[E\d{4}\]|error CS?\d{4}: "
         r"|\b(?:SyntaxError|IndentationError): |undefined reference to "),
    _sig("code", "lint", "high", "linter or formatter check failed",
         r"^\s*\d+:\d+\s+error\s+\S|^\S+\.py:\d+:\d+: [A-Z]{1,4}\d{2,4}:? "
         r"|^\S+\.rb:\d+:\d+: [CWEF]: |Code style issues found|^would reformat |^In \S+ line \d+:$"),
    # The remaining infrastructure signatures.
    _sig("infrastructure", "rate-limit", "high", "registry or API rate limit hit",
         r"(?i)toomanyrequests|too many requests|rate limit (?:exceeded|reached)"
         r"|reached your pull rate limit|rate.?limited|\bcode E429\b"),
    _sig("infrastructure", "runner", "high", "runner or agent failed or lost contact",
         r"Job failed \(system failure\)|runner system failure|Agent went offline during the build"
         r"|ChannelClosedException|Unexpected termination of the channel|Cannot contact \S+: "
         r"|was marked offline|Cannot connect to the Docker daemon|ERROR: Preparation failed"
         r"|runner has received a shutdown signal|lost communication with the server"),
    _sig("infrastructure", "runner", "medium",
         "process was terminated with SIGTERM (exit 143): the job was cancelled or the runner stopped",
         _EXIT + r"143\b|Received cancellation signal|^Terminated$"),
    _sig("infrastructure", "timeout", "medium", "time limit reached, or no output for too long",
         r"Too long with no output|No output has been received in the last|execution took longer than"
         r"|Build timed out \(after|Cancelling nested steps due to timeout|Timeout has been exceeded"
         r"|has exceeded the maximum execution time"),
    _sig("infrastructure", "credentials", "high", "credential, token or permission was rejected",
         r"(?i)\b401 Unauthorized|\b403 Forbidden|authentication required|authentication failed"
         r"|Permission denied \(publickey|could not read Username for"
         r"|requested access to the resource is denied|HTTP Basic: Access denied"
         r"|Unable to locate credentials|no basic auth credentials|status code: 40[13]\b|\bcode E40[13]\b"),
    _sig("infrastructure", "network", "high", "dependency download, registry or network request failed",
         r"\b(?:ECONNRESET|ETIMEDOUT|EAI_AGAIN|ENOTFOUND|EHOSTUNREACH|ENETUNREACH|ERR_SOCKET_TIMEOUT)\b"
         r"|Could not resolve host|Temporary failure (?:in name resolution|resolving)"
         r"|Name or service not known|no such host|Connection reset by peer|Connection timed out"
         r"|Network is unreachable|TLS handshake timeout|i/o timeout|net/http: request canceled"
         r"|socket hang up|Max retries exceeded with url|ReadTimeoutError|Read timed out"
         r"|\b50[234] (?:Bad Gateway|Service Unavailable|Gateway Time-?out)|returned error: 50\d"
         r"|Received status code 50\d|\bcode E50[0234]\b|Could not transfer artifact"
         r"|Failed to fetch https?://|Unable to fetch some archives|curl: \((?:5|6|7|28|35|52|56)\)"
         r"|error pulling image|ErrImagePull|ImagePullBackOff"),
    _sig("infrastructure", "network", "medium",
         "connection refused: a remote service was down, or a service container was not ready",
         r"(?i)\bECONNREFUSED\b|connection refused"),
    _sig("infrastructure", "cache-artifact", "medium", "cache or artifact transfer failed",
         r"(?i)failed to (?:extract|create|upload|download|restore|save|fetch) (?:the )?(?:cache|artifacts?)"
         r"|error (?:restoring|saving|uploading|downloading) (?:the )?(?:cache|artifacts?)"
         r"|^(?:ERROR|FATAL): Uploading artifacts|too large archive|Request Entity Too Large"),
    # Consequences, then the remaining code signatures.
    _sig("unknown", "missing-output", "medium",
         "a step found no files to collect, usually because an earlier step produced nothing",
         r"No test report files were found|No artifacts found that match the file pattern"
         r"|ERROR: No files to upload|No files were found with the provided path"),
    _sig("code", "test", "high", "test failed",
         r"^E\s{2,}\S|^FAILED \S+|\S::\S+ (?:FAILED|ERROR)\b|^(?:FAIL|ERROR): \S+ \(\S+\)"
         r"|\.\.\. (?:FAIL|ERROR)$|^\s*--- FAIL: |^\s*● .+ › |^\s*(?:✕|✘)\s"
         r"|^\s*FAIL\s+\S+\.(?:test|spec)\.\w+|<<< (?:FAILURE|ERROR)!|\(FAILED - \d+\)"
         r"|Tests run: \d+, Failures: (?:[1-9]\d*, Errors: \d+|0, Errors: [1-9])"
         r"|^\S.* > .+ FAILED$|^Failures:$|^rspec \./\S+:\d+|^\s*\d+ failing\b"
         r"|^test \S+ \.\.\. FAILED$|^FAILURES!$|^FAIL\s+\S+\s+[\d.]+s$"),
    _sig("code", "missing-dependency", "medium",
         "module, package or tool not found (if an install or download failed earlier, that is the cause)",
         r"ModuleNotFoundError: |ImportError: |Cannot find module |Module not found: |Can't resolve '"
         r"|cannot find package |no required module provides package|missing go\.sum entry"
         r"|cannot load such file -- |Could not find gem |No matching distribution found for "
         r"|Could not find a version that satisfies|\b(?:ETARGET|ERESOLVE)\b|\bcode E404\b"
         r"|can only install packages when your package\.json and|Your lockfile needs to be updated"
         r"|Could not find artifact |no matching package named|: (?:command )?not found$"
         r"|is not recognized as an internal or external command"),
    _sig("code", "exception", "medium", "uncaught exception",
         r"^\s*(?:Caused by: |Exception in thread \S+ )(?:[\w$]+\.)*[A-Z]\w*(?:Error|Exception)\b"
         r"|^\s*(?:[\w$]+\.)+[A-Z]\w*(?:Error|Exception)(?:: |$)"
         r"|^\s*[A-Z]\w*(?:Error|Exception): |^panic: |\bpanicked at "),
]
GENERIC = Signature("unknown", "generic-error", "low",
                    "generic error wording, no known signature", None)
EXIT_ONLY = Signature("unknown", "exit-only", "low",
                      "only an exit marker was found; the cause is in the lines above it", None)

# Lines that are never failures: passing tests, pipeline bookkeeping, Maven boilerplate.
SKIP_RE = re.compile(
    r"^\s*(?:✓|✔|√|ok\s|PASS\b|--- PASS:|=== RUN)|\.\.\. ok$"
    r"|\bPASSED(?:\s+\[\s*\d+%\])?$|^\[Pipeline\]"
    r"|^\[ERROR\] (?:COMPILATION ERROR|-> \[Help|To see the full stack trace|Re-run Maven"
    r"|For more information about the errors|\[Help \d+\] http)")
WARNING_RE = re.compile(r"^\W*(?:npm )?(?i:warn(?:ing)?)\b|: warning: ")
NOT_WARNING_RE = re.compile(r"Code style issues found")   # Prettier fails a check at [warn] level
ZERO_COUNT_RE = re.compile(
    r"(?i)\b(?:0|no) (?:errors?|failures?|failed)\b|\b(?:errors?|failures?|failed)\s*[:=]\s*0\b")
# Markers that say the job or a tool stopped. They are consequences, never the cause.
END_RE = re.compile(
    r"Job failed: (?:exit code|exit status|command terminated with exit code) [1-9]"
    r"|Exited with code exit status [1-9]|CircleCI received exit code [1-9]"
    r"|^Finished: (?:FAILURE|ABORTED|UNSTABLE)|script returned exit code [1-9]"
    r"|skipped due to earlier failure|The command exited with status [1-9]"
    r"|Process completed with exit code [1-9]|^make(?:\[\d+\])?: \*\*\* "
    r"|^npm (?:ERR!|error) (?:code ELIFECYCLE|errno -?\d|Exit status|Lifecycle script|A complete log of this run)"
    r"|Command failed with exit code [1-9]|\bELIFECYCLE\b|BUILD FAIL(?:URE|ED)\b"
    r"|FAILURE: Build failed with an exception|Failed to execute goal|Execution failed for task"
    r"|^> Task \S+ FAILED|returned a non-zero code: [1-9]"
    r"|did not complete successfully: exit code: [1-9]"
    r"|^error: (?:could not compile|test failed, to rerun|aborting due to)|^exit status [1-9]\d*$"
    r"|^FAIL$|^=+ .*\b\d+ (?:failed|errors?)\b.* =+$|^\d+ failed, "
    r"|^Tests?(?: Suites)?:\s+\d+ failed|^Found [1-9]\d* errors? in |✖ \d+ problems?")
# Error wording with no known signature. Classified as unknown, low confidence.
GENERIC_RE = re.compile(
    r"^\W*\[(?:ERROR|FATAL|ERR)\]\s*\S|^[^\w\[]*(?:ERROR|FATAL|FAILED|FAILURE)\b"
    r"|^\W*(?i:fatal error|fatal|error)\s*:"
    r"|(?<![\w./-])(?:FAILED|FAILURE)(?![\w./-])|(?<![\w./-])error: |##\[error\]"
    r"|^npm (?:ERR!|error) code ")

# The first thing to do for each category. references/failure-taxonomy.md has the detail.
RESPONSES = {
    "rate-limit": "Do not change code. Wait for the limit to reset, or authenticate or mirror the "
                  "registry. An immediate re-run usually hits the same limit.",
    "disk-full": "Do not change code. Free or enlarge the runner's disk. A re-run on the same "
                 "runner fails the same way until space is freed.",
    "out-of-memory": "Do not edit source to dodge this. Raise the job's memory, lower parallelism, "
                     "or cap the tool's heap. If memory use grew with a recent commit, that commit "
                     "is the cause.",
    "runner": "Do not change code. Re-run once. If it repeats, report it to whoever operates the "
              "runners, quoting this line.",
    "timeout": "Find where the output stops before acting. Re-run once only if nothing in the "
               "excerpt points at the code. A repeat at the same place is a hang to debug, not a "
               "limit to raise.",
    "credentials": "Do not change code and do not re-run. A token, key or permission is missing, "
                   "expired or wrong. It has to be fixed in the CI settings.",
    "network": "Do not change code. Re-run once. If it repeats, check the registry or service "
               "status and the proxy or mirror settings.",
    "cache-artifact": "Do not change code. Re-run once. If it repeats, check storage, size limits "
                      "and cache keys.",
    "missing-output": "Look above this line for the step that should have produced the files and "
                      "fix that. Do not edit the collection pattern to silence this message.",
    "test": "Reproduce the failing test locally, then fix the code or the test. Do not skip it and "
            "do not re-run until it goes green.",
    "compile": "Fix the source at the reported file and line. A re-run cannot help.",
    "type-check": "Fix the source at the reported file and line. A re-run cannot help.",
    "lint": "Fix the reported lines or run the formatter. Do not disable the rule or the step.",
    "missing-dependency": "Check that the dependency is declared and locked in the repository and "
                          "that the install step ran cleanly. A re-run helps only if an earlier "
                          "download failed.",
    "exception": "Read the stack trace around this line, reproduce locally, and fix the code. If "
                 "the exception wraps a network or resource error, classify by that error instead.",
    "generic-error": "Not classified. Read the excerpt and name the cause before any re-run or edit.",
    "exit-only": "No error line was recognised. Read the lines above the exit marker "
                 "(--context 40) to find the cause.",
}


def classify_line(text):
    """Classify one cleaned log line. Returns (kind, signature).

    kind is "error" (a failure line), "warning" (a warning that matches an
    infrastructure signature), "end" (an exit marker) or None (not a failure).
    """
    text = text[:MAX_MATCH_CHARS]
    if not text.strip() or SKIP_RE.search(text):
        return None, None
    is_warning = bool(WARNING_RE.search(text)) and not NOT_WARNING_RE.search(text)
    for sig in SIGNATURES:
        if sig.regex.search(text):
            if not is_warning:
                return "error", sig
            # A warning is not a failure, but an infrastructure warning is worth a note.
            return ("warning", sig) if sig.klass == "infrastructure" else (None, None)
    if is_warning:
        return None, None
    if END_RE.search(text):
        return "end", None
    if GENERIC_RE.search(text) and not ZERO_COUNT_RE.search(text):
        return "error", GENERIC
    return None, None


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--line", metavar="TEXT", help="classify this one cleaned log line")
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = parser.parse_args(argv)

    if args.line is None:
        rows = [{"class": s.klass, "category": s.category, "confidence": s.confidence,
                 "what": s.what, "regex": s.regex.pattern} for s in SIGNATURES]
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            for row in rows:
                print("%-15s %-19s %-7s %s" % (
                    row["class"], row["category"], row["confidence"], row["what"]))
        return 0

    kind, sig = classify_line(args.line)
    result = {"line": args.line, "kind": kind, "class": sig.klass if sig else None,
              "category": sig.category if sig else None,
              "confidence": sig.confidence if sig else None, "what": sig.what if sig else None}
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif kind == "error":
        print("failure line: %s / %s, confidence %s (%s)" % (
            sig.klass, sig.category, sig.confidence, sig.what))
    elif kind == "warning":
        print("warning, not a failure, but it matches an infrastructure signature: %s (%s)" % (
            sig.category, sig.what))
    elif kind == "end":
        print("exit marker: says the job or a tool stopped, not why")
    else:
        print("not a failure line")
    return 1 if kind == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
