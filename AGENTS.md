# Agent guide

This repo builds an interactive map of the Xian workspace. Everything an agent
needs is in `README.md`; this file only points at the parts that matter most.

- To update the map, follow **Keeping the map up to date** in `README.md`:
  run `python3 build.py --check`, fix each finding the way the table there
  describes, then `python3 build.py`.
- `capabilities.json` is the only hand-written data. Its format is described
  under **capabilities.json reference**. Verify a new runtime link with a
  source search before adding it; never invent one.
- Code changes: `build.py` (Python, stdlib only), `viewer/layout.js` (pure
  layout and routing), `viewer/index.html` (the page). Run
  `python3 -m unittest discover -s tests` before committing; it also runs the
  Node layout tests when `node` is installed.
- `out/` is generated and ignored. Never commit it. The public site is
  published only with `scripts/publish-pages.sh`, which leaves out every repo
  that is not public on GitHub; never publish a build some other way.
- Do not edit the sibling repos from here. Graph refreshes go through
  `xian-meta/scripts/graphify_workspace.py`, which only touches their ignored
  `graphify-out/` folders.
