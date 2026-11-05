import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "ci-failure-triage" / "scripts"))

import signatures  # noqa: E402

# (log line, expected class, expected category)
FAILURE_LINES = [
    # infrastructure
    ("toomanyrequests: You have reached your pull rate limit.", "infrastructure", "rate-limit"),
    ("npm ERR! code E429", "infrastructure", "rate-limit"),
    ("GitHub API rate limit exceeded for 10.0.0.1", "infrastructure", "rate-limit"),
    ("OSError: [Errno 28] No space left on device", "infrastructure", "disk-full"),
    ("fatal error: error writing to /tmp/ccA1b2.s: No space left on device",
     "infrastructure", "disk-full"),
    ("src/main.cpp:1543:1: fatal error: error writing to /tmp/ccTsHtkc.s: No space left on device",
     "infrastructure", "disk-full"),
    ("FATAL ERROR: Reached heap limit Allocation failed - JavaScript heap out of memory",
     "infrastructure", "out-of-memory"),
    ("java.lang.OutOfMemoryError: Java heap space", "infrastructure", "out-of-memory"),
    ("g++: fatal error: Killed signal terminated program cc1plus", "infrastructure", "out-of-memory"),
    ("/bin/bash: line 3:   412 Killed                  npm run build",
     "infrastructure", "out-of-memory"),
    ("ERROR: Job failed: exit code 137", "infrastructure", "out-of-memory"),
    ("Exited with code exit status 137", "infrastructure", "out-of-memory"),
    ('Received "killed" signal', "infrastructure", "out-of-memory"),
    ("\U0001F6A8 Error: The command exited with status 137", "infrastructure", "out-of-memory"),
    ('ERROR: failed to solve: process "/bin/sh -c npm ci" did not complete successfully: '
     "exit code: 137", "infrastructure", "out-of-memory"),
    ("api-1 exited with code 137", "infrastructure", "out-of-memory"),
    ("ERROR: Job failed (system failure): Cannot connect to the Docker daemon at "
     "unix:///var/run/docker.sock. Is the docker daemon running?", "infrastructure", "runner"),
    ("Agent went offline during the build", "infrastructure", "runner"),
    ("Cannot contact agent-linux-04: java.lang.InterruptedException", "infrastructure", "runner"),
    ("ERROR: Job failed: exit code 143", "infrastructure", "runner"),
    ("Too long with no output (exceeded 10m0s): context deadline exceeded",
     "infrastructure", "timeout"),
    ("ERROR: Job failed: execution took longer than 1h0m0s seconds", "infrastructure", "timeout"),
    ("Build timed out (after 30 minutes). Marking the build as aborted.",
     "infrastructure", "timeout"),
    ("Cancelling nested steps due to timeout", "infrastructure", "timeout"),
    ("fatal: Authentication failed for 'https://gitlab.example.com/acme/app.git/'",
     "infrastructure", "credentials"),
    ("remote: HTTP Basic: Access denied", "infrastructure", "credentials"),
    ("git@github.com: Permission denied (publickey).", "infrastructure", "credentials"),
    ("npm ERR! code E401", "infrastructure", "credentials"),
    ("npm ERR! code ECONNRESET", "infrastructure", "network"),
    ("npm error code ETIMEDOUT", "infrastructure", "network"),
    ("fatal: unable to access 'https://gitlab.example.com/acme/app.git/': Could not resolve "
     "host: gitlab.example.com", "infrastructure", "network"),
    ("curl: (28) Operation timed out after 30001 milliseconds with 0 bytes received",
     "infrastructure", "network"),
    ("[ERROR] Failed to execute goal on project orders: Could not resolve dependencies: Could "
     "not transfer artifact org.postgresql:postgresql:jar:42.7.3 from/to central "
     "(https://repo.maven.apache.org/maven2): Connection timed out", "infrastructure", "network"),
    ("fatal: unable to access 'https://github.com/acme/app/': The requested URL returned "
     "error: 503", "infrastructure", "network"),
    ('psycopg2.OperationalError: connection to server at "localhost" (127.0.0.1), port 5432 '
     "failed: Connection refused", "infrastructure", "network"),
    ('ERROR: Uploading artifacts as "archive" to coordinator... error',
     "infrastructure", "cache-artifact"),
    ("Failed to restore cache", "infrastructure", "cache-artifact"),
    ("db.internal:5432: error: connection refused", "infrastructure", "network"),
    # consequences of an earlier failure
    ("ERROR: No files to upload", "unknown", "missing-output"),
    ("hudson.AbortException: No test report files were found. Configuration error?",
     "unknown", "missing-output"),
    # code
    ("E       AssertionError: 403 Forbidden != 200", "code", "test"),
    ("    expect(received).toBe(expected) // Object.is equality", "code", "test"),
    ("src/cart/total.ts(14,7): error TS2322: Type 'string' is not assignable to type 'number'.",
     "code", "type-check"),
    ("src/billing/invoice.py:27: error: Incompatible return value type  [return-value]",
     "code", "type-check"),
    ("src/main.c:12:5: error: implicit declaration of function 'foo'", "code", "compile"),
    ("/src/main/java/com/acme/Foo.java:12: error: cannot find symbol", "code", "compile"),
    ("src/net/retry.c:8:14: error: 'ECONNRESET' undeclared (first use in this function)",
     "code", "compile"),
    ("src/net/retry.ts(8,14): error TS2304: Cannot find name 'ETIMEDOUT'.", "code", "type-check"),
    ("src/billing/client.py:1:80: E501 line too long (92 > 79 characters)", "code", "lint"),
    ("src/billing/client.py:1:10: E401 multiple imports on one line", "code", "lint"),
    ("[ERROR] /w/src/main/java/com/acme/OrderService.java:[42,29] cannot find symbol",
     "code", "compile"),
    ("./cmd/api/main.go:31:12: undefined: handler.NewRouter", "code", "compile"),
    ("error[E0308]: mismatched types", "code", "compile"),
    ("SyntaxError: invalid syntax", "code", "compile"),
    ("  14:7  error  'total' is assigned a value but never used  no-unused-vars", "code", "lint"),
    ("src/billing/invoice.py:3:1: F401 'os' imported but unused", "code", "lint"),
    ("app/models/order.rb:12:5: C: Style/GuardClause: Use a guard clause", "code", "lint"),
    ("would reformat src/billing/invoice.py", "code", "lint"),
    ("[warn] Code style issues found in 2 files. Run Prettier with --write to fix.",
     "code", "lint"),
    ("E       KeyError: 'total'", "code", "test"),
    ("FAILED tests/test_invoice.py::test_total - KeyError: 'total'", "code", "test"),
    ("tests/test_invoice.py::test_total FAILED                                [ 58%]",
     "code", "test"),
    ("FAIL: test_total (tests.test_invoice.InvoiceTests)", "code", "test"),
    ("test_total (tests.test_invoice.InvoiceTests) ... FAIL", "code", "test"),
    ("    ✕ rounds half up (5 ms)", "code", "test"),
    ("  ✘  7 [chromium] › tests/checkout.spec.ts:41:5 › checkout › pays with a saved card (30.0s)",
     "code", "test"),
    ("  rounds half up (FAILED - 1)", "code", "test"),
    ("--- FAIL: TestRouter (0.00s)", "code", "test"),
    ("  ● cart total › rounds half up", "code", "test"),
    ("FAIL src/cart/total.test.ts", "code", "test"),
    ("[ERROR] Tests run: 12, Failures: 1, Errors: 0, Skipped: 0", "code", "test"),
    ("OrderServiceTest > appliesTax() FAILED", "code", "test"),
    ("test billing::tests::rounds_half_up ... FAILED", "code", "test"),
    ("ModuleNotFoundError: No module named 'yaml'", "code", "missing-dependency"),
    ("Error: Cannot find module 'chalk'", "code", "missing-dependency"),
    ("sh: 1: tsc: not found", "code", "missing-dependency"),
    ("ci/build.sh: line 12: terraform: command not found", "code", "missing-dependency"),
    ("ERROR: No matching distribution found for acme-billing==9.9.9",
     "code", "missing-dependency"),
    ("npm ERR! code ERESOLVE", "code", "missing-dependency"),
    ("TypeError: Cannot read properties of undefined (reading 'total')", "code", "exception"),
    ('Exception in thread "main" java.lang.NullPointerException', "code", "exception"),
    ("panic: runtime error: index out of range [3] with length 3", "code", "exception"),
    # error wording without a known signature
    ("Error: boom", "unknown", "generic-error"),
    ("[ERROR] something odd happened", "unknown", "generic-error"),
    ("fatal: reference is not a tree: 9d4c2e7", "unknown", "generic-error"),
    ("collect2: error: ld returned 1 exit status", "unknown", "generic-error"),
]

END_MARKERS = [
    "ERROR: Job failed: exit code 1",
    "Exited with code exit status 1",
    "Finished: FAILURE",
    "ERROR: script returned exit code 1",
    "\U0001F6A8 Error: The command exited with status 1",
    "##[error]Process completed with exit code 1.",
    "make: *** [Makefile:12: test] Error 1",
    "npm ERR! code ELIFECYCLE",
    "[INFO] BUILD FAILURE",
    "> Task :compileJava FAILED",
    "=========== 1 failed, 11 passed in 0.42s ===========",
    "Tests:       1 failed, 41 passed, 42 total",
    'Stage "Integration tests" skipped due to earlier failure(s)',
    'ERROR: failed to solve: process "/bin/sh -c npm ci" did not complete successfully: exit code: 1',
]

NOT_FAILURES = [
    "",
    "[ERROR]",
    "[ERROR] COMPILATION ERROR : ",
    "[ERROR] -> [Help 1]",
    "0 errors, 2 warnings",
    "selfcheck: errors: 0, warnings: 2",
    "[INFO] Tests run: 12, Failures: 0, Errors: 0, Skipped: 0",
    "npm WARN deprecated glob@7.2.3: Glob versions prior to v9 are no longer supported",
    "src/app.c:3:5: warning: unused variable 'x' [-Wunused-variable]",
    "tests/test_client.py::test_retries_after_connection_reset_by_peer PASSED [ 16%]",
    "    ✓ retries after ECONNRESET (12 ms)",
    "test_connection_refused (tests.test_net.NetTests) ... ok",
    "ok  \tgithub.com/acme/app/net\t0.312s",
    "--- PASS: TestTimeout (0.00s)",
    "PASS src/errors/ApiError.test.ts",
    "  ApiError",
    "If you see an error, run the doctor command",
    "[Pipeline] { (Build)",
    "Job's log exceeded limit of 4194304 bytes.",
    "CircleCI received exit code 0",
    "Process finished with exit code 0",
    "status 200, body length 137",
    "retry budget: status ok, line 137",
    "mutation score: 12 killed, 3 survived",
    "Traceback (most recent call last):",
]


class ClassifyLineTests(unittest.TestCase):
    def test_failure_lines_get_the_expected_class_and_category(self):
        for text, klass, category in FAILURE_LINES:
            with self.subTest(line=text):
                kind, sig = signatures.classify_line(text)
                self.assertEqual(kind, "error")
                self.assertEqual((sig.klass, sig.category), (klass, category))

    def test_exit_markers_are_ends_not_errors(self):
        for text in END_MARKERS:
            with self.subTest(line=text):
                self.assertEqual(signatures.classify_line(text), ("end", None))

    def test_benign_lines_are_not_failures(self):
        for text in NOT_FAILURES:
            with self.subTest(line=text):
                self.assertEqual(signatures.classify_line(text), (None, None))

    def test_infrastructure_warning_is_a_warning_not_an_error(self):
        kind, sig = signatures.classify_line(
            "WARNING: Retrying (Retry(total=4)) after connection broken by "
            "'ReadTimeoutError(\"Read timed out.\")': /simple/requests/")
        self.assertEqual(kind, "warning")
        self.assertEqual(sig.category, "network")

    def test_code_warning_is_ignored(self):
        self.assertEqual(
            signatures.classify_line("warning: AssertionError was logged and ignored"), (None, None))

    def test_exit_137_inside_an_exit_marker_is_classified_as_memory(self):
        kind, sig = signatures.classify_line("ERROR: Job failed: command terminated with exit code 137")
        self.assertEqual((kind, sig.category), ("error", "out-of-memory"))

    def test_very_long_line_is_matched_only_at_its_start(self):
        padding = "x" * (signatures.MAX_MATCH_CHARS + 10)
        self.assertEqual(signatures.classify_line(padding + " No space left on device"), (None, None))
        kind, _ = signatures.classify_line("No space left on device " + padding)
        self.assertEqual(kind, "error")

    def test_every_category_has_a_response(self):
        categories = set(sig.category for sig in signatures.SIGNATURES)
        categories.update((signatures.GENERIC.category, signatures.EXIT_ONLY.category))
        self.assertEqual(categories, set(signatures.RESPONSES))

    def test_infrastructure_responses_forbid_code_changes(self):
        for sig in signatures.SIGNATURES:
            if sig.klass == "infrastructure" and sig.category != "timeout":
                with self.subTest(category=sig.category):
                    self.assertRegex(signatures.RESPONSES[sig.category], r"^Do not (change code|edit source)")


class SignaturesCliTests(unittest.TestCase):
    def run_cli(self, argv):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = signatures.main(argv)
        return code, buffer.getvalue()

    def test_listing_prints_one_row_per_signature(self):
        code, output = self.run_cli([])
        self.assertEqual(code, 0)
        self.assertEqual(len(output.splitlines()), len(signatures.SIGNATURES))

    def test_json_listing_includes_the_regular_expressions(self):
        code, output = self.run_cli(["--json"])
        rows = json.loads(output)
        self.assertEqual(code, 0)
        self.assertEqual(len(rows), len(signatures.SIGNATURES))
        self.assertTrue(all(row["regex"] for row in rows))

    def test_failure_line_exits_one(self):
        code, output = self.run_cli(["--line", "npm ERR! code ECONNRESET"])
        self.assertEqual(code, 1)
        self.assertIn("infrastructure / network", output)

    def test_failure_line_as_json(self):
        code, output = self.run_cli(["--line", "error[E0308]: mismatched types", "--json"])
        result = json.loads(output)
        self.assertEqual(code, 1)
        self.assertEqual((result["kind"], result["class"], result["category"]),
                         ("error", "code", "compile"))

    def test_benign_line_exits_zero(self):
        code, output = self.run_cli(["--line", "0 errors, 2 warnings"])
        self.assertEqual(code, 0)
        self.assertIn("not a failure line", output)

    def test_exit_marker_exits_zero(self):
        code, output = self.run_cli(["--line", "Finished: FAILURE"])
        self.assertEqual(code, 0)
        self.assertIn("exit marker", output)

    def test_unknown_option_is_a_usage_error(self):
        with self.assertRaises(SystemExit) as caught, redirect_stdout(io.StringIO()):
            stderr, sys.stderr = sys.stderr, io.StringIO()
            try:
                signatures.main(["--nope"])
            finally:
                sys.stderr = stderr
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
