# Changelog

All notable changes to this project are recorded here.
Dates are the date of the commit, not of a release.

## v1.1.0 — 2026-10-04

Defects, each reproduced first and covered by a test that failed before the fix:

- `--json` exited 0 when nothing was measured; the text report exits 2 for the
  same run. Both now take the exit code from one function.
- A FIFO inside a copy, or named as the declared version file, made the run
  block forever. Files that are not regular files are no longer opened and are
  counted as unreadable.
- A directory that could not be listed was skipped by `os.walk` without a word,
  and the coverage line said "0 files unreadable". It is now named, and
  `coverage.unreadable_dirs` is added to `--json`.
- A symlink to a directory was not entered, also without a word. It is still
  not followed, and is now named in the coverage.
- A file name that is not valid UTF-8 ended the run in a traceback with exit 1,
  the code for divergence. Such names are now hashed by their bytes.
  Fingerprints of valid UTF-8 names are unchanged.
- The default exclude `vendor/bin` never matched, because excludes were compared
  one path component at a time. An exclude with a slash now matches that run of
  directories. **Verdict change:** copies that differed only under `vendor/bin`
  now group together, as the printed "excluded" line already said.
- A copy given as a single file was fingerprinted with its own name, so the same
  content under two names was two contents. **Verdict and fingerprint change:**
  the name is no longer part of a single-file fingerprint; directory
  fingerprints are unchanged.
- A missing or malformed config, an invalid regex, a version pattern without a
  capture group, or a `declared_version` without `file` ended in a traceback
  with exit 1. **Exit code change:** these now exit 2 with one line on stderr.
  `copies` given as a string was read one character at a time; it is rejected.
- With `-c`, the command-line `--include` and `--exclude` were ignored. They now
  apply: `--exclude` adds to the config's list, `--include` replaces its filter.
- A run in which no file was read in any copy - an `--include` that matched
  nothing - reported one content and exited 0. **Exit code change:** it now
  prints "NOTHING WAS MEASURED" and exits 2. `coverage.files_measured` is added
  to `--json`.

Added:

- A unit test suite in `tests/`, standard library only, run in CI.
- `pyproject.toml`: install with pip or pipx from git, run as `samecheck`. No
  dependencies. `samecheck.py` still runs on its own.
- CI: unit tests, install and entry point, Python 3.9, 3.11 and 3.13.
- The text report names each unreadable file and directory.

## 2026-09-04

- CITATION.cff: version and date match the release. Zenodo reads this file, so a
  stale version here is a stale version in the archived record. First release
  archived by Zenodo.

## 2026-08-30

- README: remove internal hostnames and client counts
- README: link the Galaxy products the tool was built against

## 2026-08-28

- README: say where this came from, and name the service it happened on
- README: follow the renamed page
- README: name the domains, with links
- README: the set is four

## 2026-08-27

- samecheck: measure whether the copies that should be identical still are
