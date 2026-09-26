#!/usr/bin/env python3
"""Generate the tbuild-actions embedded workflow template from the default
branch's copy.

The two files cannot be byte-identical, and the difference is enforced by the
service's own test suite (workflow_test.go):

  * the template must exclude master and tbuild-ios-base from push builds;
  * it must NOT offer workflow_dispatch, because the service only reconciles
    push-triggered runs with a job, so a manual dispatch on an app branch would
    be a build nothing tracks;
  * it must keep the global concurrency guard, persist-credentials: false, the
    branding read, the artifact transfer and the isolated publish job.

The default branch's copy keeps workflow_dispatch (it is excluded from push
builds and needs a manual entry point) plus the golden and keepalive jobs,
which only ever run there.

So the template is the default branch's copy with the dispatch trigger, the
schedule trigger, the golden job and the keepalive job removed. Everything that
a build branch actually executes - build and publish - stays identical, and is
copied text rather than re-serialised, so comments and formatting survive.
"""
import pathlib
import re
import sys

MASTER = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".github/workflows/build.yml")
OUT = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "build-workflow.yml")

src = MASTER.read_text()
lines = src.splitlines(keepends=True)


def cut(lines, start_pred, end_pred, note):
    """Remove the [start_pred, end_pred) line range, asserting both anchors."""
    start = next((i for i, l in enumerate(lines) if start_pred(l)), None)
    if start is None:
        raise SystemExit("anchor not found: start of %s" % note)
    end = next((i for i, l in enumerate(lines) if i > start and end_pred(l)), None)
    if end is None:
        raise SystemExit("anchor not found: end of %s" % note)
    return lines[:start] + lines[end:]


# 1. the workflow_dispatch trigger, up to the schedule comment
lines = cut(
    lines,
    lambda l: l.startswith("  # Manual trigger."),
    lambda l: l.startswith("  # GitHub deletes cache entries"),
    "workflow_dispatch trigger",
)

# 2. the schedule trigger, up to the workflow-level permissions block
lines = cut(
    lines,
    lambda l: l.startswith("  # GitHub deletes cache entries"),
    lambda l: l.startswith("permissions:"),
    "schedule trigger",
)

# 3. the golden and keepalive jobs, up to the publish job
lines = cut(
    lines,
    lambda l: l.strip() == "# Golden cache seed. Runs only on the default branch, only on demand, and",
    lambda l: l.startswith("  publish:"),
    "golden and keepalive jobs",
)

# 4. the seed job's env entries, which no build branch consults. Matched by key
#    prefix rather than by comment text: an earlier version anchored on the
#    wording of the comment above them, and rewording that comment silently
#    broke the generator, which is the same class of mistake as anchoring a
#    numeric threshold on a guessed measurement. A comment run attached to a
#    dropped key goes with it.
DROP_ENV_PREFIXES = ("MIN_", "MAX_", "QUOTA_")
ENV_KEY = re.compile(r"^\s+[A-Za-z_][A-Za-z0-9_]*:")


def is_env_key(line):
    # A YAML mapping key, not a "k=v" line: the env entries are "KEY: value".
    return bool(ENV_KEY.match(line))


start = next((i for i, l in enumerate(lines) if l.startswith("env:")), None)
if start is None:
    raise SystemExit("anchor not found: env block")
end = start + 1
while end < len(lines) and not lines[end].startswith("jobs:"):
    end += 1

block = lines[start + 1:end]
kept = []
dropped_keys = []
i = 0
while i < len(block):
    # take the maximal run of comment lines plus at most one following key
    j = i
    while j < len(block) and block[j].strip().startswith("#"):
        j += 1
    if j < len(block) and is_env_key(block[j]) and block[j].strip().startswith(DROP_ENV_PREFIXES):
        dropped_keys.append(block[j].strip().split(":", 1)[0])
        i = j + 1          # the key and the comment run above it both go
        continue
    kept.extend(block[i:j + 1])   # comments plus the key they document
    i = j + 1
lines = lines[:start + 1] + kept + lines[end:]
if not dropped_keys:
    raise SystemExit("no seed-only env keys found; the env block layout changed")

note = """  # This copy is generated from the default branch's .github/workflows/build.yml
  # by scripts/gen-embedded-workflow.py. Edit that file, then regenerate.
  #
  # It differs from the default branch's copy in exactly one way: it has no
  # workflow_dispatch. The tbuild-actions service only reconciles push-triggered
  # runs with a job, so a manual dispatch on an app branch would start a build
  # nothing tracks. The default branch is excluded from push builds and keeps
  # its dispatch, and is also the only place the golden and keepalive jobs can
  # ever run.
"""
lines.insert(1, note)

out = "".join(lines)
OUT.write_text(out)

# the deletions above are the whole point, so assert them
for gone in ("workflow_dispatch:", "seed_cache", "  golden:", "  keepalive:",
             "MIN_CAS_KB", "MAX_CAS_KB", "MAX_REPOS_KB", "QUOTA_BYTES", "QUOTA_GIB"):
    if gone in out:
        raise SystemExit("expected %r to be absent from the template" % gone)
for kept in (
    'branches-ignore:',
    '- "master"',
    '- "tbuild-ios-base"',
    "group: tbuild-actions-${{ github.repository }}",
    "cancel-in-progress: false",
    "build-system/apply_branding.py build-system/branding/branding.json",
    "persist-credentials: false",
    "actions/upload-artifact@v4",
    "actions/download-artifact@v4",
    "  publish:",
    "      contents: write",
    "gh release create",
    "Telegram.ipa",
):
    if kept not in out:
        raise SystemExit("template lost a required fragment: %r" % kept)

print("wrote %s (%d bytes, from %d)" % (OUT, len(out), len(src)))

# The whole point of generating this file is that the parts a build branch
# executes cannot drift from the default branch's copy. Assert that on the
# parsed documents rather than on substrings, because two separate bugs in this
# script (a "k=v" test against YAML's "k: v", and kept keys being dropped along
# with the seed-only ones) both produced a file that passed every text check.
import yaml  # noqa: E402

SEED_ONLY = ("golden", "keepalive")

src_doc = yaml.safe_load(MASTER.read_text())
out_doc = yaml.safe_load(out)


def triggers(doc):
    # PyYAML resolves the bare key `on` to the boolean True.
    return doc.get("on", doc.get(True))


for field in ("permissions", "concurrency"):
    if src_doc.get(field) != out_doc.get(field):
        raise SystemExit("template changed %s: %r != %r" % (field, src_doc.get(field), out_doc.get(field)))
if triggers(src_doc)["push"] != triggers(out_doc)["push"]:
    raise SystemExit("template changed the push trigger")
def shared(env):
    return {k: v for k, v in env.items() if not k.startswith(DROP_ENV_PREFIXES)}


if shared(src_doc.get("env", {})) != out_doc.get("env", {}):
    raise SystemExit("template changed or dropped a shared env entry")

for job in ("build", "publish"):
    if src_doc["jobs"][job] != out_doc["jobs"].get(job):
        raise SystemExit("template changed the %s job; a build branch would no longer run the same steps" % job)
extra = set(out_doc["jobs"]) - {"build", "publish"}
if extra:
    raise SystemExit("template gained jobs that cannot run on a build branch: %s" % sorted(extra))

print("verified: build and publish are identical to %s; %d shared env keys" % (MASTER, len(out_doc["env"])))
