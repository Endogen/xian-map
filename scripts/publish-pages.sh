#!/usr/bin/env bash
# Publish the map to GitHub Pages (the gh-pages branch of this repo).
#
# GitHub cannot build the page itself: it needs the sibling repos and their
# local graphify graphs. So this builds it from the local workspace, leaves out
# every repo that is not public on GitHub (except the ones listed in
# show_private below), and force-pushes the page as the only commit of gh-pages
# (the site needs no history).
#
# Needs: git, python3 and an authenticated `gh`.
set -euo pipefail

# Private repos the owner chose to show on the public map anyway. Their files,
# summaries and links appear on the page; the GitHub links only open for people
# with access to the repo. Any other private repo stays hidden.
show_private=(xian-bridge)

repo_root=$(cd "$(dirname "$0")/.." && pwd)
workspace=$(dirname "$repo_root")
manifest="$workspace/xian-meta/workspace-repos.json"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# Enumerate the same workspace repos the generator can include. Only manifest
# entries have a known GitHub identity. External systems have no checkout.
python3 - "$manifest" "$repo_root/capabilities.json" > "$work/candidates" <<'PYTHON'
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

excludes=()
while IFS=$'\t' read -r name remote; do
  visibility=UNKNOWN
  if [ -n "$remote" ]; then
    visibility=$(gh repo view "$remote" --json visibility -q .visibility 2>/dev/null) || visibility=UNKNOWN
  fi
  if [ "$visibility" = "PUBLIC" ]; then
    continue
  elif [[ " ${show_private[*]} " == *" $name "* ]]; then
    echo "showing $name ($visibility, listed in show_private)"
  else
    echo "leaving out $name ($visibility)"
    excludes+=(--exclude "$name")
  fi
done < "$work/candidates"
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
