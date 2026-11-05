import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "ci-failure-triage"
FIXTURES = SKILL / "evals" / "files"
sys.path.insert(0, str(SKILL / "scripts"))

import first_failure  # noqa: E402

ESC = "\x1b"


def analyse(text, **kwargs):
    return first_failure.analyse(text, **kwargs)


def analyse_fixture(name, **kwargs):
    return first_failure.analyse(first_failure.read_log(str(FIXTURES / name)), **kwargs)


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = first_failure.main(argv)
    return code, out.getvalue(), err.getvalue()


class CleaningTests(unittest.TestCase):
    def texts(self, raw, provider="generic"):
        lines, _ = first_failure.parse(raw, provider)
        return [line.text for line in lines]

    def test_colour_codes_are_removed(self):
        self.assertEqual(self.texts(ESC + "[31;1mERROR: boom" + ESC + "[0;m\n"), ["ERROR: boom"])

    def test_buildkite_apc_timestamps_are_removed(self):
        self.assertEqual(self.texts(ESC + "_bk;t=1714560021507\x07npm ERR! code E1\n"),
                         ["npm ERR! code E1"])

    def test_iso_timestamp_prefixes_are_removed(self):
        raw = ("2024-05-01T10:40:00.103021Z plain\n"
               "[2024-05-01T10:40:13.781Z] [ERROR] bracketed\n"
               "2024-05-01 10:40:00,123 python logging\n"
               "2024-05-01T10:40:00.1234567Z ##[error]seven digits\n"
               "2024-05-01T10:40:01.902011Z 00O gitlab stream marker\n"
               "2024-05-01T10:40:01.902011Z 00E+gitlab continued line\n")
        self.assertEqual(self.texts(raw), [
            "plain", "[ERROR] bracketed", "python logging", "##[error]seven digits",
            "gitlab stream marker", "gitlab continued line"])

    def test_clock_time_prefix_is_removed_but_line_and_column_are_kept(self):
        raw = "10:40:13  [ERROR] timestamper style\n  14:7  error  'x' is unused  no-unused-vars\n"
        self.assertEqual(self.texts(raw), [
            "[ERROR] timestamper style", "  14:7  error  'x' is unused  no-unused-vars"])

    def test_timestamp_in_the_middle_of_a_line_is_kept(self):
        self.assertEqual(self.texts("[INFO] Finished at: 2024-05-01T10:40:13Z\n"),
                         ["[INFO] Finished at: 2024-05-01T10:40:13Z"])

    def test_carriage_return_keeps_what_the_terminal_shows_last(self):
        self.assertEqual(self.texts("Downloading 10%\rDownloading 100%\r\nnext\r\n"),
                         ["Downloading 100%", "next"])

    def test_line_numbers_count_newlines_only(self):
        lines, _ = first_failure.parse("one\rstill one\x0bstill one\ntwo\n", "generic")
        self.assertEqual([line.number for line in lines], [1, 2])

    def test_utf16_file_is_decoded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "utf16.log"
            path.write_bytes("step\nNo space left on device\n".encode("utf-16"))
            result = analyse(first_failure.read_log(str(path)))
        self.assertEqual(result["first_failure"]["category"], "disk-full")
        self.assertEqual(result["notes"], [
            "No step markers recognised for provider 'generic', so no step is reported."])

    def test_nul_bytes_are_noted(self):
        notes = analyse("ok\x00\x00\nKilled\n")["notes"]
        self.assertTrue(any("NUL bytes" in note for note in notes))

    def test_byte_order_mark_is_dropped_when_reading_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bom.log"
            path.write_bytes(b"\xef\xbb\xbfERROR: boom\n")
            self.assertEqual(first_failure.read_log(str(path)), "ERROR: boom\n")


class ProviderTests(unittest.TestCase):
    def test_provider_is_detected_from_markers(self):
        cases = {
            "gitlab": "section_start:1714560000:step_script\r" + ESC + "[0Khello\n",
            "jenkins": "[Pipeline] Start of Pipeline\n[Pipeline] node\n",
            "buildkite": "~~~ Running commands\n$ make test\n",
            "circleci": "#!/bin/bash -eo pipefail\nmake test\n",
            "github": "2024-05-01T10:40:00.1234567Z ##[group]Run make test\n",
            "generic": "make test\ngcc -c main.c\n",
        }
        for expected, text in cases.items():
            with self.subTest(provider=expected):
                self.assertEqual(first_failure.parse(text)[1], expected)

    def test_buildkite_is_detected_from_its_timestamps_alone(self):
        self.assertEqual(first_failure.parse(ESC + "_bk;t=1\x07make test\n")[1], "buildkite")

    def test_gitlab_sections_set_the_step_and_end_it(self):
        raw = ("section_start:1:get_sources\r" + ESC + "[0KGetting source\n"
               "fetching\n"
               "section_end:2:get_sources\r" + ESC + "[0Ksection_start:2:step_script\r" + ESC + "[0KRun\n"
               "$ make test\n"
               "make: *** [Makefile:3: test] Error 1\n"
               "section_end:3:step_script\r" + ESC + "[0K" + ESC + "[31;1mERROR: Job failed: exit code 1\n")
        lines, _ = first_failure.parse(raw)
        self.assertEqual([line.step for line in lines],
                         ["get_sources", "get_sources", "step_script", "step_script", "step_script", None])
        self.assertEqual(lines[2].text, "Run")
        self.assertEqual(lines[4].command, (4, "make test"))
        self.assertEqual(lines[5].text, "ERROR: Job failed: exit code 1")

    def test_gitlab_log_without_section_markers_uses_the_stage_headers(self):
        raw = ("Running with gitlab-runner 16.11.0 (91a27b2a)\n"
               'Executing "step_script" stage of the job script\n'
               "$ make test\n"
               "src/main.c:12:5: error: unknown type name 'foo'\n")
        first = analyse(raw)["first_failure"]
        self.assertEqual((first["step"], first["line"]), ("step_script", 4))

    def test_buildkite_groups_set_the_step_but_go_test_results_do_not(self):
        raw = ("~~~ Running commands\n$ go test ./...\n--- Unit tests\n"
               "--- FAIL: TestRouter (0.00s)\n    router_test.go:23: got 404, want 200\n")
        result = analyse(raw)
        self.assertEqual(result["provider"], "buildkite")
        first = result["first_failure"]
        self.assertEqual((first["line"], first["step"], first["category"]), (4, "Unit tests", "test"))
        self.assertEqual(first["command"], {"line": 2, "text": "go test ./..."})

    def test_jenkins_stage_names_come_from_pipeline_markers(self):
        raw = ("[Pipeline] stage\n[Pipeline] { (Unit tests)\n[Pipeline] sh\n+ make test\n"
               "main_test.go:9:2: undefined: Foo\n[Pipeline] }\n[Pipeline] // stage\n"
               "ERROR: script returned exit code 2\nFinished: FAILURE\n")
        result = analyse(raw)
        first = result["first_failure"]
        self.assertEqual((first["step"], first["line"], first["class"]), ("Unit tests", 5, "code"))
        self.assertEqual(first["command"], {"line": 4, "text": "make test"})
        self.assertEqual(result["job_end"]["line"], 9)

    def test_github_step_comes_from_the_gh_cli_prefix(self):
        raw = ("build\tRun tests\t2024-05-01T10:40:00.1234567Z ##[group]Run npm test\n"
               "build\tRun tests\t2024-05-01T10:40:01.1234567Z FAIL src/cart/total.test.ts\n"
               "build\tRun tests\t2024-05-01T10:40:02.1234567Z ##[error]Process completed with exit code 1.\n")
        result = analyse(raw)
        self.assertEqual(result["provider"], "github")
        first = result["first_failure"]
        self.assertEqual((first["line"], first["step"], first["category"]), (2, "Run tests", "test"))

    def test_byte_order_mark_inside_a_gh_cli_line_is_ignored(self):
        raw = ("build\tRun tests\t\ufeff2024-05-01T10:40:00.1234567Z ##[group]Run npm test\n"
               "build\tRun tests\t2024-05-01T10:40:01.1234567Z Killed\n")
        lines, provider = first_failure.parse(raw)
        self.assertEqual(provider, "github")
        self.assertEqual([line.text for line in lines], ["##[group]Run npm test", "Killed"])

    def test_github_step_comes_from_group_markers_in_a_raw_log(self):
        raw = ("2024-05-01T10:40:00.1234567Z ##[group]Run npm test\n"
               "2024-05-01T10:40:01.1234567Z ##[endgroup]\n"
               "2024-05-01T10:40:02.1234567Z FAIL src/cart/total.test.ts\n")
        self.assertEqual(analyse(raw)["first_failure"]["step"], "Run npm test")

    def test_provider_can_be_forced(self):
        raw = "--- Lint\n  14:7  error  'x' is unused  no-unused-vars\n"
        self.assertIsNone(analyse(raw)["first_failure"]["step"])
        self.assertEqual(analyse(raw, provider="buildkite")["first_failure"]["step"], "Lint")

    def test_command_echo_lines_are_never_failures(self):
        raw = "$ echo 'ERROR: this is only the command'\nall good\n"
        self.assertEqual(analyse(raw)["verdict"], "no-failure")


class AnalysisTests(unittest.TestCase):
    def test_first_error_wins_over_later_ones(self):
        raw = ("npm ERR! code ETIMEDOUT\n"
               "sh: 1: tsc: not found\n"
               "Error: Cannot find module 'jest'\n"
               "ERROR: Job failed: exit code 1\n")
        result = analyse(raw)
        first = result["first_failure"]
        self.assertEqual((first["line"], first["class"], first["category"]),
                         (1, "infrastructure", "network"))
        self.assertEqual(result["later_errors"]["count"], 2)
        self.assertEqual(result["later_errors"]["by_category"], {"code/missing-dependency": 2})
        self.assertTrue(any("cascade" in note for note in result["notes"]))

    def test_exit_marker_alone_is_reported_with_low_confidence(self):
        result = analyse("building\nstill building\nExited with code exit status 2\n")
        first = result["first_failure"]
        self.assertEqual(result["verdict"], "failure")
        self.assertEqual((first["line"], first["class"], first["category"], first["confidence"]),
                         (3, "unknown", "exit-only", "low"))

    def test_generic_error_takes_the_class_of_a_known_signature_just_below(self):
        raw = "ERROR: build step failed\ndetails follow\nOSError: [Errno 28] No space left on device\n"
        first = analyse(raw)["first_failure"]
        self.assertEqual((first["line"], first["class"], first["category"]),
                         (1, "infrastructure", "disk-full"))
        self.assertEqual(first["class_from_line"], 3)
        self.assertEqual(first["confidence"], "medium")

    def test_generic_error_prefers_an_infrastructure_signature_in_the_window(self):
        raw = ("Building remotely on agent-linux-04 in workspace /var/jenkins/workspace/nightly\n"
               "+ make integration\n"
               "FATAL: command execution failed\n"
               "java.io.EOFException\n"
               "\tat java.base/java.io.ObjectInputStream.readFully(ObjectInputStream.java:2933)\n"
               "Caused: java.io.IOException: Unexpected termination of the channel\n"
               "Agent went offline during the build\n"
               "Finished: FAILURE\n")
        result = analyse(raw)
        first = result["first_failure"]
        self.assertEqual(result["provider"], "jenkins")
        self.assertEqual((first["line"], first["class"], first["category"], first["confidence"]),
                         (3, "infrastructure", "runner", "medium"))
        self.assertEqual(first["class_from_line"], 6)
        self.assertEqual(first["command"], {"line": 2, "text": "make integration"})

    def test_exception_that_wraps_a_network_error_is_infrastructure(self):
        raw = ("java.lang.IllegalStateException: Failed to load ApplicationContext\n"
               "\tat org.example.Loader.load(Loader.java:12)\n"
               "Caused by: java.net.SocketTimeoutException: Read timed out\n")
        first = analyse(raw)["first_failure"]
        self.assertEqual((first["line"], first["class"], first["category"], first["class_from_line"]),
                         (1, "infrastructure", "network", 3))

    def test_exception_keeps_its_class_when_only_code_signatures_follow(self):
        raw = "KeyError: 'total'\nFAILED tests/test_invoice.py::test_total - KeyError: 'total'\n"
        first = analyse(raw)["first_failure"]
        self.assertEqual((first["class"], first["category"], first["class_from_line"]),
                         ("code", "exception", None))

    def test_generic_error_stays_unknown_when_the_known_signature_is_far_below(self):
        raw = "ERROR: build step failed\n" + "noise\n" * 12 + "No space left on device\n"
        first = analyse(raw)["first_failure"]
        self.assertEqual((first["class"], first["category"], first["confidence"]),
                         ("unknown", "generic-error", "low"))
        self.assertIsNone(first["class_from_line"])

    def test_earlier_infrastructure_warnings_lower_confidence_in_a_code_class(self):
        raw = ("WARNING: Retrying after connection broken by 'ReadTimeoutError': /simple/acme/\n"
               "ERROR: No matching distribution found for acme-billing==9.9.9\n")
        result = analyse(raw)
        first = result["first_failure"]
        self.assertEqual((first["line"], first["class"]), (2, "code"))
        self.assertEqual(first["confidence"], "medium")
        self.assertTrue(any("earlier warning" in note for note in result["notes"]))
        self.assertEqual(result["infrastructure_warnings"][0]["line"], 1)

    def test_errors_in_a_later_step_cap_confidence_and_are_noted(self):
        raw = ("~~~ Running commands\n--- Install\nNo space left on device\n"
               "--- Test\nFAIL src/cart/total.test.ts\n")
        result = analyse(raw)
        self.assertEqual(result["first_failure"]["confidence"], "medium")
        self.assertTrue(any("later step(s): Test" in note for note in result["notes"]))

    def test_errors_in_a_post_failure_step_do_not_cap_confidence(self):
        raw = ("[Pipeline] { (Build)\nsrc/main.c:12:5: error: unknown type name 'foo'\n"
               "[Pipeline] // stage\n[Pipeline] { (Declarative: Post Actions)\n"
               "No test report files were found. Configuration error?\n")
        self.assertEqual(analyse(raw)["first_failure"]["confidence"], "high")

    def test_error_logged_above_a_test_failure_is_flagged_and_capped(self):
        raw = ("PASS src/net/retry.test.ts\n"
               "    console.error\n"
               "      Error: socket hang up\n"
               "FAIL src/cart/total.test.ts\n"
               "  ● cart total › rounds half up\n")
        result = analyse(raw)
        first = result["first_failure"]
        self.assertEqual((first["line"], first["category"], first["confidence"]),
                         (3, "network", "medium"))
        self.assertTrue(any("first at line 4" in note and "passing test" in note
                            for note in result["notes"]))

    def test_command_is_kept_for_a_line_after_the_last_gitlab_section(self):
        raw = ("section_start:1:step_script\r\n$ npm run build\n> webpack --mode production\n"
               "section_end:2:step_script\rERROR: Job failed: exit code 137\n")
        first = analyse(raw)["first_failure"]
        self.assertEqual((first["line"], first["category"], first["step"], first["previous_step"]),
                         (4, "out-of-memory", None, "step_script"))
        self.assertEqual(first["command"], {"line": 2, "text": "npm run build"})

    def test_success_marker_means_error_like_lines_were_not_fatal(self):
        raw = ("section_start:1:step_script\r\n$ make deploy || true\n"
               "curl: (7) Failed to connect to deploy.example.com port 443\n"
               "section_end:2:step_script\r\nJob succeeded\n")
        result = analyse(raw)
        self.assertEqual(result["verdict"], "no-failure")
        self.assertIsNone(result["first_failure"])
        self.assertEqual(result["success_marker"]["line"], 5)
        self.assertTrue(any("not fatal" in note and "first at line 3" in note
                            for note in result["notes"]))

    def test_success_text_in_the_middle_of_a_log_is_not_a_success_marker(self):
        raw = "Job succeeded\nsrc/main.c:12:5: error: unknown type name 'foo'\n"
        result = analyse(raw)
        self.assertEqual(result["verdict"], "failure")
        self.assertIsNone(result["success_marker"])

    def test_jenkins_success_marker_wins_over_error_like_lines(self):
        raw = ("[Pipeline] { (Lint)\n  14:7  error  'x' is unused  no-unused-vars\n"
               "[Pipeline] // stage\n[Pipeline] End of Pipeline\nFinished: SUCCESS\n")
        result = analyse(raw)
        self.assertEqual(result["verdict"], "no-failure")
        self.assertEqual(result["success_marker"], {"line": 5, "text": "Finished: SUCCESS"})

    def test_truncated_log_is_noted(self):
        raw = "Job's log exceeded limit of 4194304 bytes.\nERROR: Job failed: exit code 1\n"
        self.assertTrue(any("cut the log off" in note for note in analyse(raw)["notes"]))

    def test_timeout_carries_a_note_that_it_is_not_a_cause(self):
        result = analyse("step 1 ok\nBuild timed out (after 30 minutes). Marking the build as aborted.\n")
        self.assertEqual(result["first_failure"]["category"], "timeout")
        self.assertTrue(any("not why" in note for note in result["notes"]))

    def test_excerpt_respects_context_and_marks_the_right_line(self):
        raw = "".join("line %d\n" % n for n in range(1, 10)) + "Killed\n" + "after\n" * 9
        first = analyse(raw, context=2)["first_failure"]
        self.assertEqual([row["line"] for row in first["excerpt"]], [8, 9, 10, 11, 12])
        self.assertEqual(analyse(raw, context=0)["first_failure"]["excerpt"],
                         [{"line": 10, "text": "Killed"}])

    def test_max_later_limits_the_list_but_not_the_count(self):
        raw = "No space left on device\n" * 9
        later = analyse(raw, max_later=3)["later_errors"]
        self.assertEqual((later["count"], len(later["shown"])), (8, 3))

    def test_same_input_gives_same_output(self):
        text = first_failure.read_log(str(FIXTURES / "buildkite-install-cascade.log"))
        self.assertEqual(json.dumps(analyse(text), sort_keys=True),
                         json.dumps(analyse(text), sort_keys=True))


class FixtureTests(unittest.TestCase):
    """Each fixture has one planted root cause at a known line."""

    def test_buildkite_cascade_reports_the_download_failure_not_the_missing_modules(self):
        result = analyse_fixture("buildkite-install-cascade.log")
        first = result["first_failure"]
        self.assertEqual(result["provider"], "buildkite")
        self.assertEqual((first["line"], first["text"]), (16, "npm ERR! code ECONNRESET"))
        self.assertEqual((first["class"], first["category"]), ("infrastructure", "network"))
        self.assertEqual(first["step"], ":npm: Installing dependencies")
        self.assertEqual(first["command"], {"line": 12, "text": "./ci/build.sh"})
        self.assertEqual(first["confidence"], "medium")
        self.assertEqual(result["later_errors"]["by_category"]["code/missing-dependency"], 3)
        self.assertEqual(result["job_end"]["line"], 60)

    def test_jenkins_reports_the_compile_error_not_the_missing_test_reports(self):
        result = analyse_fixture("jenkins-compile-error.log")
        first = result["first_failure"]
        self.assertEqual(result["provider"], "jenkins")
        self.assertEqual(first["line"], 41)
        self.assertIn("OrderService.java:[42,29] cannot find symbol", first["text"])
        self.assertEqual((first["class"], first["category"], first["confidence"]),
                         ("code", "compile", "high"))
        self.assertEqual(first["step"], "Build")
        self.assertEqual(first["command"]["text"], "mvn -B -ntp clean verify")
        later = analyse_fixture("jenkins-compile-error.log", max_later=50)["later_errors"]["shown"]
        missing = [row for row in later if row["category"] == "missing-output"]
        self.assertEqual([(row["line"], row["step"]) for row in missing],
                         [(76, "Declarative: Post Actions")])
        self.assertEqual(result["job_end"], {"line": 89, "text": "Finished: FAILURE"})

    def test_gitlab_test_failure_reports_the_assertion(self):
        result = analyse_fixture("gitlab-test-failure.log")
        first = result["first_failure"]
        self.assertEqual(result["provider"], "gitlab")
        self.assertEqual(first["line"], 32)
        self.assertEqual(first["text"], "E       AssertionError: assert Decimal('19.99') == Decimal('20.00')")
        self.assertEqual((first["class"], first["category"], first["confidence"]),
                         ("code", "test", "high"))
        self.assertEqual(first["step"], "step_script")
        self.assertEqual(first["command"],
                         {"line": 24, "text": "pytest -q --junitxml=reports/junit.xml"})
        self.assertEqual(result["job_end"], {"line": 50, "text": "ERROR: Job failed: exit code 1"})

    def test_gitlab_rate_limit_is_infrastructure_outside_any_step(self):
        result = analyse_fixture("gitlab-rate-limit.log")
        first = result["first_failure"]
        self.assertEqual(first["line"], 8)
        self.assertEqual((first["class"], first["category"], first["confidence"]),
                         ("infrastructure", "rate-limit", "high"))
        self.assertIsNone(first["step"])
        self.assertEqual(first["previous_step"], "prepare_executor")
        self.assertTrue(first["text"].startswith("ERROR: Job failed: failed to pull image"))
        self.assertEqual([row["line"] for row in result["infrastructure_warnings"]], [6])

    def test_circleci_timeout_shows_the_last_output_before_the_silence(self):
        result = analyse_fixture("circleci-no-output-timeout.log")
        first = result["first_failure"]
        self.assertEqual(result["provider"], "circleci")
        self.assertEqual(first["line"], 17)
        self.assertEqual((first["class"], first["category"], first["confidence"]),
                         ("infrastructure", "timeout", "medium"))
        self.assertEqual(first["step"], "npm run test:e2e")
        self.assertIn("applies a coupon", [row for row in first["excerpt"] if row["line"] == 15][0]["text"])
        self.assertEqual(result["later_errors"]["count"], 0)

    def test_passing_gitlab_job_has_no_failure(self):
        result = analyse_fixture("gitlab-passing.log")
        self.assertEqual(result["verdict"], "no-failure")
        self.assertIsNone(result["first_failure"])
        self.assertEqual(result["success_marker"], {"line": 54, "text": "Job succeeded"})
        self.assertEqual([row["line"] for row in result["infrastructure_warnings"]], [21])

    def test_passing_gitlab_job_has_no_failure_even_without_its_success_marker(self):
        text = first_failure.read_log(str(FIXTURES / "gitlab-passing.log"))
        text = text.replace("Job succeeded", "")
        result = analyse(text)
        self.assertIsNone(result["success_marker"])
        self.assertEqual(result["verdict"], "no-failure")

    def test_passing_circleci_step_has_no_failure(self):
        result = analyse_fixture("circleci-passing.log")
        self.assertEqual(result["verdict"], "no-failure")
        self.assertEqual(result["infrastructure_warnings"], [])

    def test_exit_codes_for_every_fixture(self):
        expected = {
            "buildkite-install-cascade.log": 1,
            "circleci-no-output-timeout.log": 1,
            "circleci-passing.log": 0,
            "gitlab-passing.log": 0,
            "gitlab-rate-limit.log": 1,
            "gitlab-test-failure.log": 1,
            "jenkins-compile-error.log": 1,
        }
        self.assertEqual(sorted(p.name for p in FIXTURES.glob("*.log")), sorted(expected))
        for name, code in expected.items():
            with self.subTest(fixture=name):
                self.assertEqual(run_cli([str(FIXTURES / name)])[0], code)


class CliTests(unittest.TestCase):
    def test_text_report_quotes_the_line_and_the_response(self):
        code, out, _ = run_cli([str(FIXTURES / "jenkins-compile-error.log")])
        self.assertEqual(code, 1)
        self.assertIn("FIRST FAILURE at line 41   class: code / compile   confidence: high", out)
        self.assertIn("  >    41 | [ERROR] /var/jenkins/workspace/orders_main/src/main/java/com/acme/"
                      "orders/OrderService.java:[42,29] cannot find symbol", out)
        self.assertIn("response: Fix the source at the reported file and line.", out)

    def test_json_output_is_valid_and_complete(self):
        path = str(FIXTURES / "gitlab-test-failure.log")
        code, out, _ = run_cli([path, "--json"])
        result = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual(result["log"], path)
        self.assertEqual(sorted(result), [
            "first_failure", "infrastructure_warnings", "job_end", "later_errors", "lines", "log",
            "notes", "provider", "success_marker", "verdict"])
        self.assertEqual(result["first_failure"]["line"], 32)

    def test_clean_log_prints_no_failure_found(self):
        code, out, _ = run_cli([str(FIXTURES / "circleci-passing.log")])
        self.assertEqual(code, 0)
        self.assertIn("NO FAILURE FOUND", out)

    def test_log_is_read_from_stdin(self):
        stdin = sys.stdin
        sys.stdin = io.TextIOWrapper(io.BytesIO(b"step\nKilled\n"), encoding="utf-8")
        try:
            code, out, _ = run_cli(["-", "--json"])
        finally:
            sys.stdin = stdin
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["first_failure"]["category"], "out-of-memory")

    def test_missing_file_is_an_input_error_without_a_traceback(self):
        code, out, err = run_cli([str(FIXTURES / "no-such.log")])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("cannot read log", err)

    def test_directory_is_an_input_error(self):
        self.assertEqual(run_cli([str(FIXTURES)])[0], 2)

    def test_empty_log_is_an_input_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.log"
            path.write_text("\n\n")
            code, _, err = run_cli([str(path)])
        self.assertEqual(code, 2)
        self.assertIn("empty", err)

    def test_binary_junk_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "junk.log"
            path.write_bytes(b"\xff\xfe\x00\x01 not text \x80\n" * 5)
            self.assertEqual(run_cli([str(path)])[0], 0)

    def test_scripts_run_as_programs_from_another_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = subprocess.run(
                [sys.executable, str(SKILL / "scripts" / "first_failure.py"),
                 str(FIXTURES / "gitlab-rate-limit.log"), "--json"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=tmp)
            listing = subprocess.run(
                [sys.executable, str(SKILL / "scripts" / "signatures.py"), "--json"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=tmp)
        self.assertEqual(first.returncode, 1, first.stderr)
        self.assertEqual(json.loads(first.stdout.decode("utf-8"))["first_failure"]["category"],
                         "rate-limit")
        self.assertEqual(listing.returncode, 0, listing.stderr)
        self.assertTrue(json.loads(listing.stdout.decode("utf-8")))

    def test_set_x_echo_with_several_plus_signs_is_a_command(self):
        lines, _ = first_failure.parse("++ echo 'ERROR: nested'\n+ make test\n", "generic")
        self.assertEqual([line.scan for line in lines], [False, False])
        self.assertEqual(lines[1].command, (2, "make test"))

    def test_negative_context_is_a_usage_error(self):
        with self.assertRaises(SystemExit) as caught:
            run_cli([str(FIXTURES / "circleci-passing.log"), "--context", "-1"])
        self.assertEqual(caught.exception.code, 2)

    def test_unknown_provider_is_a_usage_error(self):
        with self.assertRaises(SystemExit) as caught:
            run_cli([str(FIXTURES / "circleci-passing.log"), "--provider", "travis"])
        self.assertEqual(caught.exception.code, 2)


class ValidateRepoTests(unittest.TestCase):
    def test_repository_passes_its_own_validator(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import validate
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = validate.main(["validate.py", str(ROOT)])
        self.assertEqual(code, 0, buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
