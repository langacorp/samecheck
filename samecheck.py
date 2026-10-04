#!/usr/bin/env python3
"""samecheck - measure whether the copies that should be identical still are.

Things get copied for good reasons: a backup, a second install, a vendored
library, a version placed next to the old one. Each copy is legitimate and none
of them has an end. Later nobody knows how many versions are actually out there,
and the declared version number does not answer the question - it is a *declared*
value, not a measured one.

samecheck groups copies by the fingerprint of their CONTENT, compares that against
whatever version they claim, and shows the three-set difference between any two
groups.

It never says which copy is the right one. Choosing is a decision, and a tool that
decides quietly will one day overwrite the copy that had the thing nobody else had.

Born from a defect measured on 2026-08-27: one file existed in 29 copies across an
estate, in 4 distinct versions, under 3 different naming conventions. And in a
separate set of 47 installations of the same package, 6 distinct contents declared
only 3 version numbers - including one that claimed the same version as the
majority while differing from it.

Standard library only. MIT licensed.
"""

import argparse
import collections
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile

__version__ = "0.1.0"

# The manifest key for a copy that is one file rather than a directory. Its own
# name is left out on purpose: the same file kept as config.php, config-old.php
# and config.php.bak is one content under three names, and grouping is by
# content, never by name.
SINGLE_FILE = "."

DEFAULT_EXCLUDES = [
    "node_modules", ".git", "__pycache__", ".svn", ".hg",
    "vendor/bin", ".DS_Store", "Thumbs.db",
]


# --------------------------------------------------------------------------
# fingerprints
# --------------------------------------------------------------------------

def file_digest(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def walk(root, excludes, include=None, errors=None):
    """Relative paths of every regular file under root, SORTED.

    Sorting is not cosmetic. os.walk does not guarantee an order, and without a
    stable one two identical copies produce two different fingerprints - the tool
    would report divergence everywhere and mean nothing by it.

    A directory that cannot be listed is appended to `errors` when given. By
    default os.walk skips it without a word, and every file inside it would
    silently leave the comparison.
    """
    out = []
    if os.path.isfile(root):
        return [SINGLE_FILE]

    # An exclude with a slash ("vendor/bin") names a run of path components.
    # Compared one component at a time, as plain names are, it never matched.
    multi = [tuple(p for p in e.replace("\\", "/").split("/") if p)
             for e in excludes if "/" in e or "\\" in e]

    def excluded_run(parts):
        for m in multi:
            n = len(m)
            for i in range(len(parts) - n + 1):
                if tuple(parts[i:i + n]) == m:
                    return True
        return False

    def onerror(e):
        if errors is not None:
            where = e.filename if e.filename is not None else root
            errors.append({"path": os.path.relpath(where, root),
                           "reason": type(e).__name__})

    for dirpath, dirnames, filenames in os.walk(root, onerror=onerror):
        dirnames[:] = [d for d in dirnames if d not in excludes]
        for name in filenames:
            if name in excludes:
                continue
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            parts = rel.split(os.sep)
            if any(part in excludes for part in parts):
                continue
            if multi and excluded_run(parts[:-1]):
                continue
            if include and not re.search(include, rel):
                continue
            out.append(rel)
    out.sort()
    return out


def manifest(root, excludes, include=None, unreadable_dirs=None):
    """{relative path: sha256}, plus any file that could not be read.

    Directories that could not be listed are appended to `unreadable_dirs`
    when given.
    """
    entries, unreadable = {}, []
    base = os.path.dirname(root) if os.path.isfile(root) else root
    for rel in walk(root, excludes, include, errors=unreadable_dirs):
        full = root if os.path.isfile(root) else os.path.join(base, rel)
        try:
            # A FIFO or device is not content, and opening a FIFO for reading
            # blocks until something writes to it: the run would never end.
            if not stat.S_ISREG(os.stat(full).st_mode):
                unreadable.append({"path": rel, "reason": "not a regular file"})
                continue
            entries[rel] = file_digest(full)
        except OSError as e:
            unreadable.append({"path": rel, "reason": type(e).__name__})
    return entries, unreadable


def fingerprint(entries):
    """One hash for a whole copy: sha256 over the sorted digest+path lines."""
    h = hashlib.sha256()
    for rel in sorted(entries):
        # surrogateescape gives back the original bytes of a file name that is
        # not valid UTF-8, instead of raising. Valid names encode as before.
        h.update(f"{entries[rel]}  {rel}\n".encode("utf-8", "surrogateescape"))
    return h.hexdigest()


def shown(path):
    """A path safe to print: bytes that are not UTF-8 are written as \\xNN."""
    return path.encode("utf-8", "surrogateescape").decode("utf-8",
                                                          "backslashreplace")


def declared_version(root, spec):
    """Read the version a copy CLAIMS. {"file": ..., "pattern": ...} with one group."""
    if not spec:
        return None
    path = os.path.join(root, spec["file"]) if os.path.isdir(root) else root
    try:
        with open(path, "rb") as fh:
            data = fh.read(1 << 20)
    except OSError:
        return None
    m = re.search(spec["pattern"].encode("utf-8"), data)
    if not m:
        return None
    return m.group(1).decode("utf-8", "replace").strip()


# --------------------------------------------------------------------------
# comparison
# --------------------------------------------------------------------------

def three_sets(a_entries, b_entries):
    """The comparison that tells a superset from a divergent branch.

    Only-in-A and only-in-B alone make a bigger copy look like a newer one. The
    third set - present in both, different content - is the one that says the two
    have actually diverged.
    """
    a, b = set(a_entries), set(b_entries)
    common_differ = sorted(p for p in (a & b) if a_entries[p] != b_entries[p])
    return {"only_in_a": sorted(a - b),
            "only_in_b": sorted(b - a),
            "common_but_different": common_differ}


def measure(copies, excludes=None, include=None, version_spec=None):
    excludes = set(excludes if excludes is not None else DEFAULT_EXCLUDES)
    seen, missing = [], []
    for root in copies:
        if not os.path.exists(root):
            missing.append({"path": root, "reason": "does not exist"})
            continue
        unreadable_dirs = []
        entries, unreadable = manifest(root, excludes, include,
                                       unreadable_dirs=unreadable_dirs)
        seen.append({
            "path": os.path.abspath(root),
            "fingerprint": fingerprint(entries),
            "files": len(entries),
            "declared": declared_version(root, version_spec),
            "unreadable": unreadable,
            "unreadable_dirs": unreadable_dirs,
            "_entries": entries,
        })

    groups = collections.defaultdict(list)
    for c in seen:
        groups[c["fingerprint"]].append(c)

    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))

    # The contradiction worth naming: same claimed version, different content.
    # A tool that only groups by content sees these as two ordinary variants.
    contradictions = []
    by_declared = collections.defaultdict(set)
    for c in seen:
        if c["declared"] is not None:
            by_declared[c["declared"]].add(c["fingerprint"])
    for version, fps in sorted(by_declared.items()):
        if len(fps) > 1:
            contradictions.append({
                "declared": version,
                "distinct_contents": len(fps),
                "fingerprints": sorted(f[:12] for f in fps),
            })

    return {
        "version": __version__,
        "coverage": {
            "copies_declared": len(copies),
            "copies_measured": len(seen),
            "copies_missing": len(missing),
            "not_measured": missing,
            "unreadable_files": sum(len(c["unreadable"]) for c in seen),
            "unreadable_dirs": sum(len(c["unreadable_dirs"]) for c in seen),
            "excluded": sorted(excludes),
            "include_filter": include,
        },
        "distinct_contents": len(groups),
        "distinct_declared": len(by_declared),
        "contradictions": contradictions,
        "groups": [{
            "fingerprint": fp,
            "copies": len(members),
            "files": members[0]["files"],
            "declared": sorted({m["declared"] for m in members}, key=str),
            "paths": [m["path"] for m in members],
        } for fp, members in ordered],
        "_seen": seen,
    }


def exit_code(res):
    """0 one content, 1 divergence found, 2 nothing was measured.

    One function for the text report and --json alike: the same run must not
    exit differently depending on how its answer is printed.
    """
    if res["coverage"]["copies_measured"] == 0:
        return 2
    return 1 if res["distinct_contents"] > 1 else 0


def report(res, stream=sys.stdout, diff_against_largest=True):
    c = res["coverage"]
    stream.write(f"\n{res['distinct_contents']} distinct contents across "
                 f"{c['copies_measured']} copies")
    if res["distinct_declared"]:
        stream.write(f", {res['distinct_declared']} distinct declared versions")
    stream.write("\n")

    for g in res["groups"]:
        decl = ", ".join(str(d) for d in g["declared"] if d is not None)
        stream.write(f"\n  {g['fingerprint'][:12]}  {g['copies']} cop"
                     f"{'y' if g['copies'] == 1 else 'ies'}, {g['files']} files"
                     f"{'  declared: ' + decl if decl else ''}\n")
        for p in g["paths"][:10]:
            stream.write(f"      {shown(p)}\n")
        if len(g["paths"]) > 10:
            stream.write(f"      ... and {len(g['paths']) - 10} more\n")

    for k in res["contradictions"]:
        stream.write(f"\n  CONTRADICTION: {k['distinct_contents']} different "
                     f"contents all declare version {k['declared']}\n")
        stream.write("    The declared version does not identify the content. "
                     "Anything that trusts it is counting wrong.\n")

    if diff_against_largest and len(res["groups"]) > 1:
        seen = {c["path"]: c for c in res["_seen"]}
        ref_fp = res["groups"][0]["fingerprint"][:12]
        base = seen[res["groups"][0]["paths"][0]]
        stream.write(f"\n  three-set difference against {ref_fp}, the group with "
                     f"the most copies\n")
        for g in res["groups"][1:]:
            other = seen[g["paths"][0]]
            d = three_sets(base["_entries"], other["_entries"])
            stream.write(f"\n  {g['fingerprint'][:12]}:\n")
            for label, items in (
                    (f"only in {ref_fp}", d["only_in_a"]),
                    (f"only in {g['fingerprint'][:12]}", d["only_in_b"]),
                    ("in both, different content", d["common_but_different"])):
                stream.write(f"    {label}: {len(items)}\n")
                for p in items[:5]:
                    stream.write(f"      {shown(p)}\n")
                if len(items) > 5:
                    stream.write(f"      ... and {len(items) - 5} more\n")

    stream.write(f"\ncoverage: {c['copies_measured']}/{c['copies_declared']} "
                 f"copies measured, {c['copies_missing']} missing, "
                 f"{c['unreadable_files']} files unreadable")
    if c.get("unreadable_dirs"):
        stream.write(f", {c['unreadable_dirs']} directories unreadable")
    stream.write("\n")
    for cp in res.get("_seen", []):
        for u in cp["unreadable"]:
            stream.write(f"  unreadable file: "
                         f"{shown(os.path.join(cp['path'], u['path']))}"
                         f" - {u['reason']}\n")
        for u in cp.get("unreadable_dirs", []):
            stream.write(f"  unreadable directory: "
                         f"{shown(os.path.join(cp['path'], u['path']))} - "
                         f"{u['reason']} - nothing inside it was measured\n")
    for m in c["not_measured"]:
        stream.write(f"  not measured: {shown(m['path'])} - {m['reason']}\n")
    if c["include_filter"]:
        stream.write(f"  only files matching: {c['include_filter']} - "
                     f"divergence outside this filter was not measured\n")
    stream.write(f"  excluded: {', '.join(c['excluded'])}\n")
    stream.write("  measured divergence is a LOWER bound, never an upper one\n")

    stream.write("\nsamecheck does not say which copy is the right one. "
                 "That is a decision.\n")

    if c["copies_measured"] == 0:
        stream.write("NOTHING WAS MEASURED. This is not a pass.\n")
    return exit_code(res)


# --------------------------------------------------------------------------
# self-test
# --------------------------------------------------------------------------

def _write(root, files):
    for name, content in files.items():
        full = os.path.join(root, name)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(content)
    return root


def selftest(stream=sys.stdout):
    failures = []
    tmp = tempfile.mkdtemp(prefix="samecheck-selftest-")
    vspec = {"file": "plugin.php", "pattern": r"Version:\s*([0-9.]+)"}
    try:
        base = {
            "plugin.php": b"<?php\n// Version: 2.7.9\n",
            "lib/a.php": b"<?php\necho 'a';\n",
            "lib/b.php": b"<?php\necho 'b';\n",
            "assets/style.css": b"body{color:#000}\n",
        }

        # ---- direction 1: must fire on copies that differ
        one = _write(os.path.join(tmp, "d1", "one"), base)
        two = _write(os.path.join(tmp, "d1", "two"), dict(base, **{
            "lib/b.php": b"<?php\necho 'b changed';\n"}))
        three = _write(os.path.join(tmp, "d1", "three"), dict(base, **{
            "lib/c.php": b"<?php\necho 'c';\n"}))
        r = measure([one, two, three], version_spec=vspec)
        stream.write("direction 1 - must fire on copies that differ\n")
        stream.write(f"  distinct contents: {r['distinct_contents']} "
                     f"(expected 3)\n")
        if r["distinct_contents"] != 3:
            failures.append(f"expected 3 distinct contents, got "
                            f"{r['distinct_contents']}")
        d = three_sets(manifest(one, set(DEFAULT_EXCLUDES))[0],
                       manifest(three, set(DEFAULT_EXCLUDES))[0])
        if len(d["only_in_b"]) != 1 or d["only_in_b"][0] != os.path.join("lib", "c.php"):
            failures.append("the three-set diff did not isolate the added file")
        d2 = three_sets(manifest(one, set(DEFAULT_EXCLUDES))[0],
                        manifest(two, set(DEFAULT_EXCLUDES))[0])
        if len(d2["common_but_different"]) != 1:
            failures.append("the three-set diff did not isolate the changed file")
        stream.write(f"  three-set diff: added={len(d['only_in_b'])}, "
                     f"changed={len(d2['common_but_different'])}\n")

        # ---- direction 2: must stay silent on identical copies
        a = _write(os.path.join(tmp, "d2", "a"), base)
        b = _write(os.path.join(tmp, "d2", "b"), base)
        os.makedirs(os.path.join(b, "node_modules", "junk"), exist_ok=True)
        with open(os.path.join(b, "node_modules", "junk", "x.js"), "wb") as fh:
            fh.write(b"// vendored, excluded by default\n")
        r2 = measure([a, b], version_spec=vspec)
        stream.write("direction 2 - must stay silent on identical copies\n")
        stream.write(f"  distinct contents: {r2['distinct_contents']} "
                     f"(expected 1)\n")
        if r2["distinct_contents"] != 1:
            failures.append("identical copies were reported as divergent")
        if r2["contradictions"]:
            failures.append("identical copies produced a contradiction")

        # ---- direction 3: the contradiction must be named
        p = _write(os.path.join(tmp, "d3", "p"), base)
        q = _write(os.path.join(tmp, "d3", "q"), dict(base, **{
            "lib/a.php": b"<?php\necho 'a different';\n"}))  # same Version line
        r3 = measure([p, q], version_spec=vspec)
        stream.write("direction 3 - same declared version, different content, "
                     "must be named\n")
        stream.write(f"  contradictions: {len(r3['contradictions'])} "
                     f"(expected 1)\n")
        if len(r3["contradictions"]) != 1:
            failures.append("a same-version different-content pair was not "
                            "reported as a contradiction")
        elif r3["contradictions"][0]["declared"] != "2.7.9":
            failures.append("the contradiction named the wrong version")

        # ---- direction 4: an empty run must not look like a pass
        r4 = measure([os.path.join(tmp, "does-not-exist")])
        rc = report(r4, open(os.devnull, "w"))
        stream.write("direction 4 - an empty run is not a pass\n")
        stream.write(f"  copies measured: {r4['coverage']['copies_measured']}, "
                     f"exit code would be {rc}\n")
        if r4["coverage"]["copies_measured"] != 0:
            failures.append("a missing path was counted as measured")
        if rc == 0:
            failures.append("a run that measured nothing exited 0")

        # ---- direction 5: sorting must not change the fingerprint
        e1, _ = manifest(a, set(DEFAULT_EXCLUDES))
        e2 = dict(reversed(list(e1.items())))
        if fingerprint(e1) != fingerprint(e2):
            failures.append("the fingerprint depended on insertion order")
        stream.write("direction 5 - fingerprint must not depend on file order\n")
        stream.write("  stable\n")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        stream.write("\nSELFTEST FAILED\n")
        for f in failures:
            stream.write(f"  {f}\n")
        return 1
    stream.write("\nselftest passed: the check fires on divergence and is "
                 "silent on identical copies\n")
    return 0


# --------------------------------------------------------------------------

class ConfigError(ValueError):
    """A config or option that cannot be used as given."""


def check_regex(value, where, groups=0):
    try:
        rx = re.compile(value)
    except (re.error, TypeError) as e:
        raise ConfigError(f"{where}: not a valid regular expression: {e}")
    if rx.groups < groups:
        raise ConfigError(f"{where}: needs one capture group, e.g. "
                          f"Version:\\s*([0-9.]+)")
    return value


def check_version_spec(spec):
    if spec is None:
        return None
    if (not isinstance(spec, dict) or not isinstance(spec.get("file"), str)
            or not isinstance(spec.get("pattern"), str)):
        raise ConfigError('declared_version: needs "file" and "pattern", '
                          'both strings')
    check_regex(spec["pattern"], "declared_version.pattern", groups=1)
    return spec


def load_config(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError) as e:
        raise ConfigError(f"cannot read config {path}: {e}")
    if not isinstance(cfg, dict):
        raise ConfigError(f"config {path}: must be a JSON object")
    copies = cfg.get("copies", [])
    excludes = cfg.get("exclude", DEFAULT_EXCLUDES)
    # A string here would be read one character at a time: "/srv/x" would
    # become the copies "/", "s", "r", ... and "/" would be walked in full.
    if not isinstance(copies, list) or not all(isinstance(c, str)
                                               for c in copies):
        raise ConfigError(f"config {path}: copies must be a list of paths")
    if not isinstance(excludes, list) or not all(isinstance(e, str)
                                                 for e in excludes):
        raise ConfigError(f"config {path}: exclude must be a list of names")
    include = cfg.get("include")
    if include is not None:
        check_regex(include, "include")
    return (copies, excludes, include,
            check_version_spec(cfg.get("declared_version")))


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="samecheck",
        description="Measure whether the copies that should be identical still "
                    "are. Never says which one is right.")
    p.add_argument("copies", nargs="*", help="paths to compare")
    p.add_argument("-c", "--config", help="JSON file with copies and options")
    p.add_argument("--exclude", action="append", default=None,
                   help="name to skip (repeatable); replaces the defaults, or "
                        "adds to the config's list")
    p.add_argument("--include", help="only files whose path matches this "
                                     "regex; overrides the config's")
    p.add_argument("--json", action="store_true")
    p.add_argument("--selftest", action="store_true",
                   help="prove the check in both directions and exit")
    p.add_argument("--version", action="version", version=__version__)
    args = p.parse_args(argv)

    if args.selftest:
        return selftest()

    version_spec = None
    try:
        if args.config:
            copies, excludes, include, version_spec = load_config(args.config)
            # Command-line values are not dropped next to a config: copies and
            # excludes are added to its lists, --include replaces its filter.
            if args.copies:
                copies = list(copies) + args.copies
            if args.exclude is not None:
                excludes = list(excludes) + args.exclude
            if args.include is not None:
                include = check_regex(args.include, "--include")
        else:
            copies = args.copies
            excludes = (args.exclude if args.exclude is not None
                        else DEFAULT_EXCLUDES)
            include = args.include
            if include is not None:
                check_regex(include, "--include")
    except ConfigError as e:
        # A traceback exits 1, which is the code for "divergence found".
        p.error(str(e))
    if not copies:
        p.error("give at least two paths, or a --config (or use --selftest)")

    res = measure(copies, excludes=excludes, include=include,
                  version_spec=version_spec)
    if args.json:
        out = {k: v for k, v in res.items() if not k.startswith("_")}
        json.dump(out, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return exit_code(res)
    return report(res)


if __name__ == "__main__":
    sys.exit(main())
