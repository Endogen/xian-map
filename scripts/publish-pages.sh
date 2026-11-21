#!/usr/bin/env bash
# Publish the map to GitHub Pages (the gh-pages branch of this repo).
#
# GitHub cannot build the page itself: it needs the sibling repos and their
# local graphify graphs. So this builds it from the local workspace, leaves out
# every repo that is not public on GitHub, and force-pushes the page as the only
# commit of gh-pages (the site needs no history).
#
# Needs: git, python3 and an authenticated `gh`.
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/.." && pwd)
workspace=$(dirname "$repo_root")
manifest="$workspace/xian-meta/workspace-repos.json"
org=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["organization"])' "$manifest")

# Leave out every local repo that GitHub does not report as public.
excludes=()
while IFS=$'\t' read -r name slug; do
  [ -d "$workspace/$name/.git" ] || continue
  visibility=$(gh repo view "$org/$slug" --json visibility -q .visibility 2>/dev/null || echo UNKNOWN)
  if [ "$visibility" != "PUBLIC" ]; then
    echo "leaving out $name ($visibility)"
    excludes+=(--exclude "$name")
  fi
done < <(python3 -c '
import json, sys
for repo in json.load(open(sys.argv[1]))["repos"]:
    print(repo["name"], repo.get("slug", repo["name"]), sep="\t")
' "$manifest")

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
python3 "$repo_root/build.py" --out "$work/build" ${excludes[@]+"${excludes[@]}"}

mkdir "$work/site"
cp "$work/build/code-map.html" "$work/site/index.html"
touch "$work/site/.nojekyll"
source_commit=$(git -C "$repo_root" rev-parse --short HEAD)
remote=$(git -C "$repo_root" remote get-url origin)
git -C "$work/site" init -q -b gh-pages
git -C "$work/site" add -A
git -C "$work/site" commit -q -m "Publish map built from xian-map $source_commit"
git -C "$work/site" push -q --force "$remote" gh-pages
echo "published to gh-pages"
