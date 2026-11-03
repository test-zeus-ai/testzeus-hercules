#!/usr/bin/env bash
# Merge the source branch (main) into the target branch (dev) and push it.
# On a merge conflict, open (or update) a back-merge PR into the target instead.
#
# Inputs (environment):
#   SOURCE_BRANCH      branch to merge from (default: main)
#   TARGET_BRANCH      branch to keep up to date (default: dev)
#   REMOTE             git remote name (default: origin)
#   BACKMERGE_BRANCH   branch used for the conflict PR (default: backmerge/<source>-into-<target>)
#   GITHUB_REPOSITORY  owner/repo, used for links (set by GitHub Actions)
#   GITHUB_SERVER_URL  default: https://github.com
#   GH_TOKEN           token for the gh CLI (only needed for the conflict path)
#
# Exit codes: 0 = target up to date (merged, already current, or conflict PR opened/updated);
#             1 = conflict and no PR could be opened (an issue was attempted), or push failed.
set -euo pipefail

SOURCE_BRANCH="${SOURCE_BRANCH:-main}"
TARGET_BRANCH="${TARGET_BRANCH:-dev}"
REMOTE="${REMOTE:-origin}"
BACKMERGE_BRANCH="${BACKMERGE_BRANCH:-backmerge/${SOURCE_BRANCH}-into-${TARGET_BRANCH}}"
SERVER_URL="${GITHUB_SERVER_URL:-https://github.com}"
REPO="${GITHUB_REPOSITORY:-}"
PR_TITLE="Backmerge ${SOURCE_BRANCH} to ${TARGET_BRANCH}"

log() { printf '%s\n' "$*"; }

git fetch --no-tags "$REMOTE" "$SOURCE_BRANCH"
if ! git ls-remote --exit-code --heads "$REMOTE" "$TARGET_BRANCH" >/dev/null; then
  log "Branch '$TARGET_BRANCH' does not exist on '$REMOTE'; nothing to do."
  exit 0
fi
git fetch --no-tags "$REMOTE" "$TARGET_BRANCH"

source_ref="refs/remotes/$REMOTE/$SOURCE_BRANCH"
target_ref="refs/remotes/$REMOTE/$TARGET_BRANCH"
source_sha="$(git rev-parse "$source_ref")"
short_sha="$(git rev-parse --short "$source_ref")"

if git merge-base --is-ancestor "$source_ref" "$target_ref"; then
  log "'$TARGET_BRANCH' already contains '$SOURCE_BRANCH' ($short_sha); nothing to do."
  exit 0
fi

git checkout --quiet -B "$TARGET_BRANCH" "$target_ref"
if git merge --no-ff --no-edit -m "chore: backmerge ${SOURCE_BRANCH} into ${TARGET_BRANCH} (${short_sha})" "$source_ref"; then
  git push "$REMOTE" "HEAD:refs/heads/$TARGET_BRANCH"
  log "Merged '$SOURCE_BRANCH' ($short_sha) into '$TARGET_BRANCH' and pushed."
  exit 0
fi

# --- conflict path ---------------------------------------------------------
# shellcheck disable=SC2016  # literal backticks: Markdown code spans in the PR body
conflicts="$(git diff --name-only --diff-filter=U | sed 's/^/- `/; s/$/`/')"
git merge --abort
log "Merge of '$SOURCE_BRANCH' into '$TARGET_BRANCH' conflicts in:"
log "$conflicts"

existing_pr="$(gh pr list --head "$BACKMERGE_BRANCH" --base "$TARGET_BRANCH" --state open --json number --jq '.[0].number // empty' 2>/dev/null || true)"
if [ -n "$existing_pr" ]; then
  # Never rewrite a branch someone may be resolving conflicts on; just note that main moved.
  gh pr comment "$existing_pr" --body "\`${SOURCE_BRANCH}\` advanced to ${short_sha} and still conflicts with \`${TARGET_BRANCH}\` in:

${conflicts}

Merge the latest \`${SOURCE_BRANCH}\` into this branch while resolving."
  log "Updated existing back-merge PR #$existing_pr."
  exit 0
fi

git push --force "$REMOTE" "${source_sha}:refs/heads/${BACKMERGE_BRANCH}"

body="Automatic back-merge of \`${SOURCE_BRANCH}\` (${short_sha}) into \`${TARGET_BRANCH}\` hit conflicts in:

${conflicts}

To resolve:
\`\`\`bash
git fetch ${REMOTE}
git checkout ${BACKMERGE_BRANCH}
git merge ${REMOTE}/${TARGET_BRANCH}
# resolve, commit, push
\`\`\`
Then merge this PR with a **merge commit** (not squash), so \`${TARGET_BRANCH}\` records \`${SOURCE_BRANCH}\` as merged."

if gh pr create --base "$TARGET_BRANCH" --head "$BACKMERGE_BRANCH" --title "$PR_TITLE" --body "$body"; then
  log "Opened back-merge PR for conflicts."
  exit 0
fi

compare_url="${SERVER_URL}/${REPO}/compare/${TARGET_BRANCH}...${BACKMERGE_BRANCH}"
log "::error::Back-merge conflicted and a PR could not be opened (Actions may not be allowed to create PRs; set the BACKMERGE_TOKEN secret). Compare: ${compare_url}"
gh issue create --title "${PR_TITLE}: conflicts need resolving" --body "${body}

A pull request could not be opened automatically. Open one from: ${compare_url}" || true
exit 1
