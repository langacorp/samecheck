# samecheck

Measure whether the copies that should be identical still are — and **never say
which one is right**.

## The defect it was born from

**2026-08-27.** Two measurements on the same day, in the same estate:

One configuration file existed in **29 copies**, in **4 distinct versions**, under
**3 different naming conventions**. Nobody knew that until it was counted.

Separately, one of our own components — a plugin running on dozens of client
sites through [link.langa.tv](https://link.langa.tv) — was installed **47 times**. It had **6 distinct contents**
and declared only **3 version numbers** — including one install that claimed the
same version as the majority while differing from it, and one that carried the
*highest* version number while having **lost** five files the others had.

Both findings share a shape: **the declared version number does not identify the
content.** It is a *declared* value, not a measured one. Anything that trusts it is
counting wrong, and no completeness check will notice, because the field is full.

## What it does

For every copy it builds a manifest — `sha256` per file, relative path — and
reduces it to one **fingerprint**: `sha256` over the *sorted* digest-and-path lines.

Sorting is not cosmetic. `os.walk` does not guarantee an order, and without a stable
one two identical copies produce two different fingerprints; the tool would report
divergence everywhere and mean nothing by it.

Then it:

- **groups by content**, never by name and never by declared version;
- **compares the declared version against the measured content**, and names the
  contradiction when the same claimed version covers different contents. A tool that
  only groups by content sees those as two ordinary variants rather than as a lie;
- shows the **three-set difference** between groups: only in A, only in B, **and in
  both but different**. The third set is the one that distinguishes a superset from
  a divergent branch. Without it, a copy with two extra files looks like a newer
  version even when it has lost five others.

## What it will not do

It will not tell you which copy is the correct one, and it will not align anything.

Choosing is a decision. A tool that decides quietly will one day overwrite the copy
that had the thing nobody else had — the translation, the local fix, the field that
was only ever set in one place.

## Coverage is always declared

Every run states how many copies were measured out of how many declared, which ones
were missing, how many files could not be read, and what was excluded.

If an `include` filter was used, the run says so — divergence outside the filter was
not measured. **Measured divergence is a lower bound, never an upper one.** A partial
coverage can call two things identical when they are not, and it always errs in the
same direction.

A run that measured nothing prints `NOTHING WAS MEASURED. This is not a pass.` and
exits `2`.

## Install

None. Python 3.8+, standard library only.

```
curl -O https://raw.githubusercontent.com/langacorp/samecheck/main/samecheck.py
python3 samecheck.py --selftest
```

## Prove it before you trust it

```
python3 samecheck.py --selftest
```

Five directions, all asserted, any one failing fails the test:

1. **must fire** on copies that differ — one by a changed file, one by an added file
2. **must stay silent** on identical copies, including when one has a `node_modules`
   the other lacks
3. **must name the contradiction** — same declared version, different content
4. **must not look like a pass** when nothing was measured
5. **the fingerprint must not depend on file order** — the same manifest fed in
   reverse must produce the same fingerprint

## Use

```
python3 samecheck.py /srv/site-a/plugin /srv/site-b/plugin /srv/site-c/plugin
python3 samecheck.py -c copies.json --json
```

`copies.example.json`:

```json
{
  "copies": [
    "/srv/site-a/wp-content/plugins/example",
    "/srv/site-b/wp-content/plugins/example"
  ],
  "exclude": ["node_modules", ".git", "cache"],
  "include": "\\.php$",
  "declared_version": {
    "file": "example.php",
    "pattern": "Version:\\s*([0-9.]+)"
  }
}
```

`declared_version` is optional; without it the tool groups by content only and
cannot name contradictions.

Exit codes: `0` one content · `1` divergence found · `2` nothing was measured.

## Limits, stated

- It compares files on disk. Database rows, registered cron jobs and licence state
  are not files, are not measured, and can make two copies with the same fingerprint
  behave differently.
- Two files can be functionally equivalent and byte-different — a timestamp in a
  header, different line endings. Those are reported as divergence, because they
  are, and deciding they do not matter is a decision.
- `--include` makes a run faster and the answer smaller. It never makes it safer.

## The other two

`realroute`, `leakform` and `samecheck` came out of the same week of measuring.
Each one is standalone and depends on neither of the others.

- **[realroute](https://github.com/langacorp/realroute)** — checks that a route
  really exists, by content and not by status code. Born from a site that answered
  `200` to every URL, so a status-code check called it green without having looked
  at anything.
- **[leakform](https://github.com/langacorp/leakform)** — finds secrets in a git
  repository by shape, across every ref. Born from a repository that was searched
  for the first time five years after its last commit.

What they share: coverage is always declared, a run that examined nothing is never
a pass, and each ships a self-test that must fire in one direction and stay silent
in the other.


## Where this comes from

LANGA runs 16 digital services across 5 networks on its own infrastructure. This
tool came out of a defect we hit while running them. See
[How we work](https://about.langa.tv/how-we-work/).

---

## License

MIT. See `LICENSE`.

---

Built and maintained by LANGA.
