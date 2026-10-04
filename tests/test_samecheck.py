"""Unit tests for samecheck.

Every verdict is exercised in both directions: a test that can only pass
proves nothing. Fixtures are built in temporary directories and removed
afterwards; nothing here needs the network.
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import samecheck  # noqa: E402

SCRIPT = os.path.join(ROOT, "samecheck.py")

BASE = {
    "plugin.php": b"<?php\n// Version: 2.7.9\n",
    "lib/a.php": b"<?php\necho 'a';\n",
    "lib/b.php": b"<?php\necho 'b';\n",
    "assets/style.css": b"body{color:#000}\n",
}
VSPEC = {"file": "plugin.php", "pattern": r"Version:\s*([0-9.]+)"}


def write(root, files):
    for name, content in files.items():
        full = os.path.join(root, *name.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(content)
    return root


def run_main(argv):
    """Run main() in-process; return (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = samecheck.main(argv)
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue(), err.getvalue()


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="samecheck-test-")

    def tearDown(self):
        for dirpath, dirnames, _ in os.walk(self.tmp):
            for d in dirnames:
                try:
                    os.chmod(os.path.join(dirpath, d), 0o755)
                except OSError:
                    pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def copy(self, name, files=None, **changes):
        files = dict(BASE if files is None else files)
        # Keyword names stand for paths: lib__a_php -> lib/a.php. Keys that
        # already contain a dot or a slash are taken as written.
        for k, v in changes.items():
            if "." not in k and "/" not in k:
                k = k.replace("__", "/")
                head, _, ext = k.rpartition("_")
                k = f"{head}.{ext}"
            files[k] = v
        return write(os.path.join(self.tmp, name), files)


class FingerprintTests(TmpCase):
    def test_order_does_not_change_fingerprint(self):
        e, _ = samecheck.manifest(self.copy("a"), set(samecheck.DEFAULT_EXCLUDES))
        rev = dict(reversed(list(e.items())))
        self.assertEqual(samecheck.fingerprint(e), samecheck.fingerprint(rev))

    def test_content_change_changes_fingerprint(self):
        ex = set(samecheck.DEFAULT_EXCLUDES)
        a, _ = samecheck.manifest(self.copy("a"), ex)
        b, _ = samecheck.manifest(self.copy("b", lib__a_php=b"x"), ex)
        self.assertNotEqual(samecheck.fingerprint(a), samecheck.fingerprint(b))

    def test_rename_changes_fingerprint_inside_a_directory(self):
        ex = set(samecheck.DEFAULT_EXCLUDES)
        files = dict(BASE)
        files["lib/renamed.php"] = files.pop("lib/a.php")
        a, _ = samecheck.manifest(self.copy("a"), ex)
        b, _ = samecheck.manifest(self.copy("b", files), ex)
        self.assertNotEqual(samecheck.fingerprint(a), samecheck.fingerprint(b))

    def test_file_digest_is_sha256(self):
        p = os.path.join(self.tmp, "f")
        with open(p, "wb") as fh:
            fh.write(b"abc")
        self.assertEqual(
            samecheck.file_digest(p),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")


class ThreeSetTests(unittest.TestCase):
    def test_each_set(self):
        a = {"same": "1", "changed": "1", "gone": "1"}
        b = {"same": "1", "changed": "2", "new": "1"}
        d = samecheck.three_sets(a, b)
        self.assertEqual(d["only_in_a"], ["gone"])
        self.assertEqual(d["only_in_b"], ["new"])
        self.assertEqual(d["common_but_different"], ["changed"])

    def test_identical_gives_three_empty_sets(self):
        a = {"x": "1", "y": "2"}
        d = samecheck.three_sets(a, dict(a))
        self.assertEqual(d, {"only_in_a": [], "only_in_b": [],
                             "common_but_different": []})


class MeasureTests(TmpCase):
    def test_identical_copies_one_group(self):
        r = samecheck.measure([self.copy("a"), self.copy("b")])
        self.assertEqual(r["distinct_contents"], 1)
        self.assertEqual(r["groups"][0]["copies"], 2)

    def test_changed_and_added_are_distinct(self):
        r = samecheck.measure([self.copy("a"),
                               self.copy("b", lib__b_php=b"changed"),
                               self.copy("c", lib__c_php=b"new")])
        self.assertEqual(r["distinct_contents"], 3)

    def test_groups_ordered_by_size(self):
        r = samecheck.measure([self.copy("odd", lib__a_php=b"x"),
                               self.copy("a"), self.copy("b")])
        self.assertEqual(r["groups"][0]["copies"], 2)
        self.assertEqual(r["groups"][1]["copies"], 1)

    def test_default_excludes_node_modules(self):
        b = self.copy("b", **{"node_modules/junk/x.js": b"//"})
        r = samecheck.measure([self.copy("a"), b])
        self.assertEqual(r["distinct_contents"], 1)

    def test_default_excludes_vendor_bin(self):
        b = self.copy("b", **{"vendor/bin/tool": b"#!/bin/sh\n"})
        r = samecheck.measure([self.copy("a"), b])
        self.assertEqual(r["distinct_contents"], 1)

    def test_nested_vendor_bin_excluded_but_vendor_is_not(self):
        b = self.copy("b", **{"sub/vendor/bin/tool": b"x"})
        self.assertEqual(
            samecheck.measure([self.copy("a"), b])["distinct_contents"], 1)
        c = self.copy("c", **{"vendor/lib/x.php": b"x"})
        self.assertEqual(
            samecheck.measure([self.copy("a2"), c])["distinct_contents"], 2)

    def test_multi_part_exclude_is_not_a_substring_match(self):
        c = self.copy("c", **{"myvendor/bin/x": b"x", "vendor/binary/y": b"y"})
        self.assertEqual(
            samecheck.measure([self.copy("a"), c])["distinct_contents"], 2)

    def test_explicit_empty_excludes_sees_node_modules(self):
        b = self.copy("b", **{"node_modules/junk/x.js": b"//"})
        r = samecheck.measure([self.copy("a"), b], excludes=[])
        self.assertEqual(r["distinct_contents"], 2)

    def test_include_filter_hides_divergence_outside_it(self):
        b = self.copy("b", assets__style_css=b"changed")
        self.assertEqual(
            samecheck.measure([self.copy("a"), b])["distinct_contents"], 2)
        r = samecheck.measure([self.copy("a2"), b], include=r"\.php$")
        self.assertEqual(r["distinct_contents"], 1)
        self.assertEqual(r["coverage"]["include_filter"], r"\.php$")

    def test_missing_copy_is_declared(self):
        r = samecheck.measure([self.copy("a"), os.path.join(self.tmp, "nope")])
        self.assertEqual(r["coverage"]["copies_declared"], 2)
        self.assertEqual(r["coverage"]["copies_measured"], 1)
        self.assertEqual(r["coverage"]["copies_missing"], 1)

    def test_unreadable_file_is_counted(self):
        a = self.copy("a")
        real = samecheck.file_digest

        def flaky(path, *args, **kw):
            if path.endswith("b.php"):
                raise PermissionError(13, "denied")
            return real(path, *args, **kw)

        with mock.patch.object(samecheck, "file_digest", flaky):
            r = samecheck.measure([a])
        self.assertEqual(r["coverage"]["unreadable_files"], 1)
        self.assertEqual(r["_seen"][0]["unreadable"][0]["reason"],
                         "PermissionError")

    def test_no_unreadable_on_clean_copy(self):
        r = samecheck.measure([self.copy("a")])
        self.assertEqual(r["coverage"]["unreadable_files"], 0)


class SpecialFileTests(TmpCase):
    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs os.mkfifo")
    def test_fifo_is_not_read_and_is_declared(self):
        # Opening a FIFO for reading blocks until a writer appears. Run in a
        # subprocess with a timeout so a regression fails instead of hanging.
        a = self.copy("a")
        b = self.copy("b")
        os.mkfifo(os.path.join(b, "pipe"))
        try:
            p = subprocess.run([sys.executable, SCRIPT, "--json", a, b],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=20)
        except subprocess.TimeoutExpired:
            self.fail("samecheck blocked on a FIFO")
        data = json.loads(p.stdout)
        self.assertEqual(data["coverage"]["unreadable_files"], 1)
        self.assertEqual(data["distinct_contents"], 1)
        self.assertEqual(p.returncode, 0)

    def test_broken_symlink_is_unreadable(self):
        b = self.copy("b")
        os.symlink(os.path.join(self.tmp, "nowhere"), os.path.join(b, "dead"))
        r = samecheck.measure([b])
        self.assertEqual(r["coverage"]["unreadable_files"], 1)


@unittest.skipIf(sys.platform in ("win32", "darwin"),
                 "needs a file system that accepts non-UTF-8 names")
class NonUtf8NameTests(TmpCase):
    def _copy_with_raw_name(self, name, raw, content=b"x"):
        root = self.copy(name)
        with open(os.path.join(os.fsencode(root), raw), "wb") as fh:
            fh.write(content)
        return root

    def test_fingerprint_does_not_crash(self):
        a = self._copy_with_raw_name("a", b"caf\xe9.txt")
        b = self._copy_with_raw_name("b", b"caf\xe9.txt")
        r = samecheck.measure([a, b])
        self.assertEqual(r["distinct_contents"], 1)

    def test_divergence_still_found(self):
        a = self._copy_with_raw_name("a", b"caf\xe9.txt", b"1")
        b = self._copy_with_raw_name("b", b"caf\xe9.txt", b"2")
        self.assertEqual(samecheck.measure([a, b])["distinct_contents"], 2)

    def test_fingerprint_of_utf8_names_unchanged(self):
        # The fix must not move the fingerprint of copies that worked before.
        entries = {"lib/\u00e9.php": "0" * 64}
        expected = __import__("hashlib").sha256(
            ("0" * 64 + "  lib/\u00e9.php\n").encode("utf-8")).hexdigest()
        self.assertEqual(samecheck.fingerprint(entries), expected)

    def test_text_report_on_strict_utf8_stream(self):
        a = self._copy_with_raw_name("a", b"caf\xe9.txt", b"1")
        b = self._copy_with_raw_name("b", b"caf\xe9.txt", b"2")
        c = self._copy_with_raw_name("c", b"caf\xe9.txt", b"2")
        r = samecheck.measure([a, b, c])
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="utf-8", errors="strict")
        self.assertEqual(samecheck.report(r, stream), 1)
        stream.flush()
        self.assertIn(b"caf\\xe9.txt", raw.getvalue())


class UnreadableDirectoryTests(TmpCase):
    def _two_copies_with_secret(self):
        a = self.copy("a", **{"secret/k": b"1"})
        b = self.copy("b", **{"secret/k": b"2"})
        return a, b

    def test_unlistable_directory_is_declared(self):
        # Simulated, so it also runs as root, where chmod does not stop reads.
        a, b = self._two_copies_with_secret()
        real = os.scandir

        def deny(path="."):
            if os.path.basename(os.fspath(path)) == "secret":
                raise PermissionError(13, "Permission denied", path)
            return real(path)

        with mock.patch("os.scandir", deny):
            r = samecheck.measure([a, b])
        self.assertEqual(r["coverage"]["unreadable_dirs"], 2)
        self.assertEqual(r["_seen"][0]["unreadable_dirs"][0],
                         {"path": "secret", "reason": "PermissionError"})
        s = io.StringIO()
        samecheck.report(r, s)
        self.assertIn("2 directories unreadable", s.getvalue())

    def test_listable_directory_is_not_declared(self):
        a, b = self._two_copies_with_secret()
        r = samecheck.measure([a, b])
        self.assertEqual(r["coverage"]["unreadable_dirs"], 0)
        self.assertEqual(r["distinct_contents"], 2)

    @unittest.skipIf(not hasattr(os, "geteuid") or os.geteuid() == 0,
                     "chmod does not stop root from reading")
    def test_chmod_000_directory(self):
        a, b = self._two_copies_with_secret()
        for root in (a, b):
            os.chmod(os.path.join(root, "secret"), 0)
        r = samecheck.measure([a, b])
        self.assertEqual(r["coverage"]["unreadable_dirs"], 2)


class DeclaredVersionTests(TmpCase):
    def test_contradiction_named(self):
        r = samecheck.measure([self.copy("p"),
                               self.copy("q", lib__a_php=b"different")],
                              version_spec=VSPEC)
        self.assertEqual(len(r["contradictions"]), 1)
        self.assertEqual(r["contradictions"][0]["declared"], "2.7.9")
        self.assertEqual(r["contradictions"][0]["distinct_contents"], 2)

    def test_no_contradiction_when_versions_differ(self):
        r = samecheck.measure(
            [self.copy("p"),
             self.copy("q", plugin_php=b"<?php\n// Version: 2.8.0\n")],
            version_spec=VSPEC)
        self.assertEqual(r["distinct_contents"], 2)
        self.assertEqual(r["distinct_declared"], 2)
        self.assertEqual(r["contradictions"], [])

    def test_no_contradiction_on_identical(self):
        r = samecheck.measure([self.copy("p"), self.copy("q")],
                              version_spec=VSPEC)
        self.assertEqual(r["contradictions"], [])

    def test_missing_version_file_is_none(self):
        files = dict(BASE)
        del files["plugin.php"]
        self.assertIsNone(
            samecheck.declared_version(self.copy("p", files), VSPEC))

    def test_no_match_is_none(self):
        p = self.copy("p", plugin_php=b"<?php\n")
        self.assertIsNone(samecheck.declared_version(p, VSPEC))

    def test_no_spec_is_none(self):
        self.assertIsNone(samecheck.declared_version(self.copy("p"), None))


class ReportTests(TmpCase):
    def report(self, res):
        s = io.StringIO()
        return samecheck.report(res, s), s.getvalue()

    def test_exit_0_on_one_content(self):
        rc, out = self.report(samecheck.measure([self.copy("a"), self.copy("b")]))
        self.assertEqual(rc, 0)
        self.assertIn("1 distinct contents across 2 copies", out)

    def test_exit_1_on_divergence_with_three_sets(self):
        rc, out = self.report(samecheck.measure(
            [self.copy("a"), self.copy("b"),
             self.copy("c", lib__a_php=b"x", lib__new_php=b"n")]))
        self.assertEqual(rc, 1)
        self.assertIn("three-set difference", out)
        self.assertIn("in both, different content: 1", out)

    def test_exit_2_on_nothing_measured(self):
        rc, out = self.report(samecheck.measure([os.path.join(self.tmp, "x")]))
        self.assertEqual(rc, 2)
        self.assertIn("NOTHING WAS MEASURED", out)

    def test_contradiction_printed(self):
        _, out = self.report(samecheck.measure(
            [self.copy("p"), self.copy("q", lib__a_php=b"d")],
            version_spec=VSPEC))
        self.assertIn("CONTRADICTION", out)

    def test_include_filter_stated(self):
        _, out = self.report(samecheck.measure(
            [self.copy("a"), self.copy("b")], include=r"\.php$"))
        self.assertIn("divergence outside this filter was not measured", out)


class CliTests(TmpCase):
    def test_cli_exit_codes(self):
        a, b = self.copy("a"), self.copy("b")
        c = self.copy("c", lib__a_php=b"x")
        self.assertEqual(run_main([a, b])[0], 0)
        self.assertEqual(run_main([a, c])[0], 1)
        self.assertEqual(run_main([os.path.join(self.tmp, "nope")])[0], 2)

    def test_json_exit_codes_and_fields(self):
        a, b = self.copy("a"), self.copy("b")
        c = self.copy("c", lib__a_php=b"x")
        rc, out, _ = run_main(["--json", a, b])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["distinct_contents"], 1)
        self.assertFalse(any(k.startswith("_") for k in data))
        rc, out, _ = run_main(["--json", a, c])
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(out)["distinct_contents"], 2)

    def test_json_exit_2_when_nothing_measured(self):
        rc, out, _ = run_main(["--json", os.path.join(self.tmp, "nope")])
        self.assertEqual(json.loads(out)["coverage"]["copies_measured"], 0)
        self.assertEqual(rc, 2)

    def test_no_paths_is_usage_error(self):
        self.assertEqual(run_main([])[0], 2)

    def test_config_file(self):
        a = self.copy("a")
        b = self.copy("b", lib__a_php=b"different")
        cfg = os.path.join(self.tmp, "c.json")
        with open(cfg, "w") as fh:
            json.dump({"copies": [a, b], "declared_version": VSPEC}, fh)
        rc, out, _ = run_main(["-c", cfg, "--json"])
        self.assertEqual(rc, 1)
        self.assertEqual(len(json.loads(out)["contradictions"]), 1)

    def test_cli_exclude_replaces_defaults(self):
        a = self.copy("a")
        b = self.copy("b", **{"node_modules/x.js": b"//", "cache/y": b"y"})
        self.assertEqual(run_main([a, b])[0], 1)  # cache is not a default
        self.assertEqual(
            run_main(["--exclude", "node_modules", "--exclude", "cache",
                      a, b])[0], 0)

    def test_version_flag(self):
        rc, out, _ = run_main(["--version"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), samecheck.__version__)

    def test_selftest_subprocess(self):
        p = subprocess.run([sys.executable, SCRIPT, "--selftest"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn(b"selftest passed", p.stdout)

    def test_selftest_fails_when_the_check_is_broken(self):
        # The self-test must be able to fail: break grouping, expect exit 1.
        with mock.patch.object(samecheck, "fingerprint", lambda e: "same"):
            self.assertEqual(samecheck.selftest(io.StringIO()), 1)


if __name__ == "__main__":
    unittest.main()
