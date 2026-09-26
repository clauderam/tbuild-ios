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

# 4. the golden job's budget slice, which only the seed step consults. It is the
#    last group of env entries, so consume it as a run of non-blank lines.
start = next((i for i, l in enumerate(lines)
              if l.strip().startswith("# Budget slices against")), None)
if start is None:
    raise SystemExit("anchor not found: budget comment")
end = start
while end < len(lines) and lines[end].strip() != "" and not lines[end].startswith("jobs:"):
    end += 1
if end < len(lines) and lines[end].strip() == "":
    end += 1  # the blank line that separated it from the next block
lines = lines[:start] + lines[end:]

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
for gone in ("workflow_dispatch:", "seed_cache", "  golden:", "  keepalive:", "MIN_CAS_KB"):
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
