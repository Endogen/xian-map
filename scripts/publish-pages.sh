#!/usr/bin/env bash
# Publish the map to GitHub Pages (the gh-pages branch of this repo).
#
# With no arguments, build and publish from the local workspace.
# CI builds and publishes in separate jobs so the build has read-only access.
set -euo pipefail

# Private repos the owner chose to show on the public map anyway. Their files,
# summaries and links appear on the page; the GitHub links only open for people
# with access to the repo. Any other private repo stays hidden.
show_private=(xian-bridge)

repo_root=$(cd "$(dirname "$0")/.." && pwd)
workspace=$(dirname "$repo_root")
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

build_site() {
  local site=$1 fresh_workspace=$2 name remote visibility
  local -a excludes=()

  # Only manifest entries have a known GitHub identity. External systems have no checkout.
  python3 - "$workspace/xian-meta/workspace-repos.json" "$repo_root/capabilities.json" > "$work/candidates" <<'PYTHON'
import json
import sys

with open(sys.argv[1]) as f:
    manifest = json.load(f)
with open(sys.argv[2]) as f:
    repos = json.load(f)["repos"]
slugs = {r["name"]: r.get("slug", r["name"]) for r in manifest["repos"]}
for name in sorted(slugs.keys() | repos.keys()):
    if not name.startswith(".") and not repos.get(name, {}).get("external"):
        remote = f"{manifest['organization']}/{slugs[name]}" if name in slugs else ""
        print(name, remote, sep="\t")
PYTHON

  while IFS=$'\t' read -r name remote; do
    visibility=UNKNOWN
    if [ -n "$remote" ]; then
      visibility=$(gh repo view "$remote" --json visibility -q .visibility 2>/dev/null) || visibility=UNKNOWN
    fi
    if [ "$visibility" = PUBLIC ]; then
      if [ "$fresh_workspace" = yes ] && [ ! -e "$workspace/$name/.git" ]; then
        git clone --quiet --depth 1 "https://github.com/$remote.git" "$workspace/$name"
      fi
    elif [[ " ${show_private[*]} " == *" $name "* ]]; then
      echo "showing $name ($visibility, listed in show_private)"
      if [ "$fresh_workspace" = yes ] && [ ! -e "$workspace/$name/.git" ]; then
        echo "Missing the approved checkout for $name" >&2
        return 1
      fi
    else
      echo "leaving out $name ($visibility)"
      excludes+=(--exclude "$name")
      continue
    fi
    if [ "$fresh_workspace" = yes ]; then
      git -C "$workspace/$name" log -1 --format="$name %H"
    fi
  done < "$work/candidates"

  if [ "$fresh_workspace" = yes ]; then
    python3 "$workspace/xian-meta/scripts/graphify_workspace.py" refresh
  fi
  python3 "$repo_root/build.py" --out "$work/build" ${excludes[@]+"${excludes[@]}"}
  mkdir -p "$site"
  cp "$work/build/code-map.html" "$site/index.html"
}

publish_site() {
  local index=$1 source_commit remote previous main
  source_commit=$(git -C "$repo_root" rev-parse HEAD)
  remote=$(git -C "$repo_root" remote get-url --push origin)
  previous=$(git ls-remote "$remote" refs/heads/gh-pages | cut -f1)
  if [ "$2" = ci ]; then
    main=$(git ls-remote "$remote" refs/heads/main | cut -f1)
    if [ "$source_commit" != "$main" ]; then
      echo "Skipping Pages deployment because main has moved to $main"
      return
    fi
  fi

  mkdir "$work/deploy"
  cp "$index" "$work/deploy/index.html"
  touch "$work/deploy/.nojekyll"
  git -C "$work/deploy" init -q -b gh-pages
  git -C "$work/deploy" add index.html .nojekyll
  git -C "$work/deploy" commit -q -m "Publish map built from xian-map ${source_commit:0:7}"
  git -C "$work/deploy" push -q --force-with-lease="refs/heads/gh-pages:$previous" "$remote" gh-pages
  echo "published to gh-pages"

  # Pushes authenticated with GITHUB_TOKEN do not trigger branch-based builds.
  if [ "$2" = ci ]; then
    gh api --method POST "repos/$GITHUB_REPOSITORY/pages/builds" --jq .status
  fi
}

case "${1:-}" in
  "")
    build_site "$work/site" no
    publish_site "$work/site/index.html" manual
    ;;
  --ci-build)
    build_site "${2:?pass the site output directory}" yes
    ;;
  --ci-publish)
    publish_site "${2:?pass the path to index.html}" ci
    ;;
  *)
    echo "Usage: $0 [--ci-build DIRECTORY | --ci-publish INDEX_HTML]" >&2
    exit 2
    ;;
esac
