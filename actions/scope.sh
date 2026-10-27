#!/usr/bin/env bash
# Prints in_scope=true|false to $GITHUB_OUTPUT: is any file this PR touches
# outside the SKIP_PREFIXES? Shared by every gate (narthelix/ops#2050) -- it
# used to be copied into each workflow.
#
# Computed in shell rather than via a paths-filter template: interpolating a
# multiline input into the action's `filters:` string drops the continuation
# lines' indentation and produces invalid YAML. Prefix matching is equivalent
# for the `specs/`-style exclusions in use.
#
# Env: SKIP_PREFIXES, EVENT_NAME, BASE_SHA, HEAD_SHA.
set -euo pipefail
if [ -z "${SKIP_PREFIXES// }" ] || { [ "$EVENT_NAME" != "pull_request" ] && [ "$EVENT_NAME" != "merge_group" ]; }; then
  echo "in_scope=true" >> "$GITHUB_OUTPUT"; exit 0
fi
if ! git cat-file -e "${BASE_SHA}^{commit}" 2>/dev/null; then
  # Base unreachable (force-push): fail open into scope rather than silently
  # skipping a gate.
  echo "in_scope=true" >> "$GITHUB_OUTPUT"; exit 0
fi
in_scope=false
while IFS= read -r f; do
  [ -z "$f" ] && continue
  skip=false
  for p in $SKIP_PREFIXES; do
    case "$f" in "$p"*) skip=true ;; esac
  done
  [ "$skip" = true ] || in_scope=true
done < <(git diff --name-only "$BASE_SHA" "$HEAD_SHA")
echo "in_scope=$in_scope" >> "$GITHUB_OUTPUT"
