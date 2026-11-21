#!/usr/bin/env python3
"""Build an interactive map of the workspace.

The map shows every repo as an area, the capabilities inside it (from the
curated taxonomy in capabilities.json), the languages each capability
is written in, and the links between capabilities:

- code:     calls/imports inside a repo, taken from graphify-out/graph.json
- import:   Python and JS/TS imports that cross repo boundaries
- protocol: curated runtime links (RPC, ABCI, SQL, native bindings, ...)
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import fnmatch
import json
from pathlib import Path
import re
import subprocess
import sys


REPO_ROOT = Path(__file__).absolute().parent
DEFAULT_TAXONOMY = REPO_ROOT / "capabilities.json"
DEFAULT_VIEWER = REPO_ROOT / "viewer/index.html"
DEFAULT_LAYOUT = REPO_ROOT / "viewer/layout.js"
VENDOR_DIR = REPO_ROOT / "viewer/vendor"
MANIFEST_IN_WORKSPACE = "xian-meta/workspace-repos.json"
# Load order matters: React, ReactDOM, the jsxRuntime shim, then React Flow.
VENDOR_SCRIPTS = ("react.production.min.js", "react-dom.production.min.js", "jsx-runtime-shim.js", "xyflow-react.umd.js", "elk.bundled.js")
VENDOR_STYLES = ("xyflow-react.css",)
DATA_PLACEHOLDER = '"__CODE_MAP_DATA__"'
LAYOUT_PLACEHOLDER = "/*__CODE_MAP_LAYOUT__*/"
VENDOR_JS_PLACEHOLDER = "/*__CODE_MAP_VENDOR_JS__*/"
VENDOR_CSS_PLACEHOLDER = "/*__CODE_MAP_VENDOR_CSS__*/"

LANGUAGE_BY_EXTENSION = {
    "py": "Python",
    "ts": "TypeScript",
    "tsx": "TypeScript",
    "js": "JavaScript",
    "jsx": "JavaScript",
    "mjs": "JavaScript",
    "rs": "Rust",
    "go": "Go",
    "kt": "Kotlin",
    "swift": "Swift",
    "sh": "Shell",
    "c": "C",
    "h": "C",
    "vue": "Vue",
    "sol": "Solidity",
}
CONTRACT_LANGUAGE = "Xian contract"
SCRIPT_LANGUAGES = {"TypeScript", "JavaScript", "Vue"}
NON_SOURCE = re.compile(
    r"(^|/)(tests?|__tests__|vendor|node_modules|dist|build|docs|fixtures|examples?|\.github)(/|$)"
    r"|\.(test|spec)\.[^/]+$"
    r"|(^|/)(test_[^/]*|conftest\.py)$"
)
CODE_RELATIONS = {
    "calls",
    "imports",
    "imports_from",
    "indirect_call",
    "inherits",
    "implements",
    "extends",
    "re_exports",
    "uses",
}
SCRIPT_IMPORT = re.compile(
    r"""(?:\bfrom\s*|\bimport\s*\(\s*|\brequire\s*\(\s*|^[ \t]*import[ \t]+)(['"])([^'"\n]+)\1""",
    re.M,
)


# --- source files -----------------------------------------------------------


def language_of(path: str, contract: bool = False) -> str | None:
    _, dot, extension = path.rpartition("/")[2].rpartition(".")
    language = LANGUAGE_BY_EXTENSION.get(extension) if dot else None
    if contract and language == "Python":
        return CONTRACT_LANGUAGE
    return language


def is_source(path: str) -> bool:
    return language_of(path) is not None and not NON_SOURCE.search(path)


# --- taxonomy ---------------------------------------------------------------


class TaxonomyError(ValueError):
    pass


@dataclass(frozen=True)
class Capability:
    id: str
    repo: str
    name: str
    summary: str
    tags: tuple[str, ...]
    paths: tuple[str, ...]
    languages: tuple[str, ...]
    contract: bool


@dataclass(frozen=True)
class RepoSpec:
    name: str
    summary: str
    external: bool
    capabilities: tuple[Capability, ...]


@dataclass(frozen=True)
class Link:
    source: str
    target: str
    kind: str
    label: str


@dataclass(frozen=True)
class Taxonomy:
    tags: tuple[str, ...]
    repos: dict[str, RepoSpec]
    links: tuple[Link, ...]


def parse_taxonomy(raw: dict) -> Taxonomy:
    tags = tuple(raw.get("tags", []))
    repos = {name: _parse_repo(name, spec, set(tags)) for name, spec in raw.get("repos", {}).items()}
    known = _unique_capability_ids(repos.values())
    links = tuple(_parse_link(link, known) for link in raw.get("links", []))
    return Taxonomy(tags=tags, repos=repos, links=links)


def _parse_repo(name: str, spec: dict, tags: set[str]) -> RepoSpec:
    external = bool(spec.get("external", False))
    capabilities = tuple(_parse_capability(name, cap, external, tags) for cap in spec.get("capabilities", []))
    return RepoSpec(name=name, summary=spec.get("summary", ""), external=external, capabilities=capabilities)


def _parse_capability(repo: str, spec: dict, external: bool, tags: set[str]) -> Capability:
    cap_id = f"{repo}/{spec['id']}"
    for tag in spec.get("tags", []):
        if tag not in tags:
            raise TaxonomyError(f"{cap_id}: unknown tag '{tag}'")
    paths = tuple(spec.get("paths", []))
    languages = tuple(spec.get("languages", []))
    if external and not languages:
        raise TaxonomyError(f"{cap_id} needs languages")
    if not external and not paths:
        raise TaxonomyError(f"{cap_id} needs paths")
    return Capability(
        id=cap_id,
        repo=repo,
        name=spec["name"],
        summary=spec.get("summary", ""),
        tags=tuple(spec.get("tags", [])),
        paths=paths,
        languages=languages,
        contract=bool(spec.get("contract", False)),
    )


def _unique_capability_ids(repos) -> set[str]:
    seen: set[str] = set()
    for repo in repos:
        for cap in repo.capabilities:
            if cap.id in seen:
                raise TaxonomyError(f"duplicate capability '{cap.id}'")
            seen.add(cap.id)
    return seen


def _parse_link(raw: dict, known: set[str]) -> Link:
    for end in (raw["from"], raw["to"]):
        if end not in known:
            raise TaxonomyError(f"link {raw['from']} -> {raw['to']}: unknown capability '{end}'")
    return Link(source=raw["from"], target=raw["to"], kind=raw["kind"], label=raw.get("label", raw["kind"]))


def assign_capability(path: str, capabilities) -> Capability | None:
    for cap in capabilities:
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in cap.paths):
            return cap
    return None


def fallback_group(path: str) -> str:
    return "/".join(path.split("/")[:-1][:2]) or "."


# --- imports ----------------------------------------------------------------


def python_module_index(files) -> dict[str, str]:
    package_dirs = {f.rsplit("/", 1)[0] for f in files if f.endswith("/__init__.py")}
    index: dict[str, str] = {}
    for path in files:
        if path.endswith(".py"):
            index[_python_module_name(path, package_dirs)] = path
    return index


def _python_module_name(path: str, package_dirs: set[str]) -> str:
    parts = path[: -len(".py")].split("/")
    start = len(parts) - 1
    while "/".join(parts[:start]) in package_dirs:
        start -= 1
    module = parts[start:]
    if module[-1] == "__init__":
        module = module[:-1]
    return ".".join(module)


def python_imports(source: str) -> list[tuple[str, tuple[str, ...], int]]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, (), node.lineno, node.col_offset) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node.module, tuple(alias.name for alias in node.names), node.lineno, node.col_offset))
    return [(module, names, line) for module, names, line, _ in sorted(found, key=lambda item: item[2:])]


def resolve_python_import(module: str, names, index: dict):
    for name in names:
        candidate = index.get(f"{module}.{name}")
        if candidate is not None:
            return candidate
    parts = module.split(".")
    for end in range(len(parts), 0, -1):
        candidate = index.get(".".join(parts[:end]))
        if candidate is not None:
            return candidate
    return None


def script_imports(source: str) -> list[tuple[str, int]]:
    return [(match.group(2), source.count("\n", 0, match.start()) + 1) for match in SCRIPT_IMPORT.finditer(source)]


def resolve_package(spec: str, packages: dict):
    if spec in packages:
        return packages[spec]
    for name, location in packages.items():
        if spec.startswith(name + "/"):
            return location
    return None


# --- edges ------------------------------------------------------------------


class EdgeSet:
    def __init__(self, max_examples: int = 3):
        self.max_examples = max_examples
        self._edges: dict[tuple[str, str, str], dict] = {}

    def add(self, source: str, target: str, kind: str, example: str | None = None) -> None:
        if source == target:
            return
        key = (source, target, kind)
        edge = self._edges.setdefault(key, {"from": source, "to": target, "kind": kind, "weight": 0, "examples": []})
        edge["weight"] += 1
        if example and len(edge["examples"]) < self.max_examples:
            edge["examples"].append(example)

    def as_list(self) -> list[dict]:
        return [self._edges[key] for key in sorted(self._edges)]


def add_graph_edges(edges: EdgeSet, graph: dict, file_caps: dict[str, str]) -> None:
    nodes = {node["id"]: node for node in graph.get("nodes", [])}
    for link in graph.get("links", []):
        if link.get("relation") not in CODE_RELATIONS:
            continue
        source, target = nodes.get(link.get("source")), nodes.get(link.get("target"))
        if source is None or target is None:
            continue
        source_cap = file_caps.get(source.get("source_file"))
        target_cap = file_caps.get(target.get("source_file"))
        if source_cap and target_cap:
            edges.add(source_cap, target_cap, "code", _graph_example(link, source, target))


def _graph_example(link: dict, source: dict, target: dict) -> str:
    file = link.get("source_file") or source.get("source_file")
    location = link.get("source_location") or source.get("source_location")
    where = f"{file}:{location}" if location else file
    return f"{where} {source.get('label')} {link['relation']} {target.get('label')}"


# --- workspace scan ---------------------------------------------------------


@dataclass
class RepoScan:
    name: str
    root: Path
    spec: RepoSpec
    files: dict[str, tuple[str, str, int]]  # path -> (capability id, language, loc)
    fallback: dict[str, str]  # fallback capability id -> group path


def tracked_files(repo_root: Path) -> list[str]:
    result = subprocess.run(["git", "-C", str(repo_root), "ls-files", "-z"], capture_output=True, check=True)
    return [path for path in result.stdout.decode().split("\0") if path]


def read_source(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8", errors="ignore")
    except OSError:
        return ""


def count_loc(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip())


def scan_repo(name: str, root: Path, spec: RepoSpec) -> RepoScan:
    scan = RepoScan(name=name, root=root, spec=spec, files={}, fallback={})
    for path in filter(is_source, tracked_files(root)):
        cap = assign_capability(path, spec.capabilities)
        cap_id = cap.id if cap else _fallback_id(scan, path)
        language = language_of(path, contract=bool(cap and cap.contract))
        scan.files[path] = (cap_id, language, count_loc(read_source(root / path)))
    return scan


def _fallback_id(scan: RepoScan, path: str) -> str:
    group = fallback_group(path)
    cap_id = f"{scan.name}/~{group}"
    scan.fallback[cap_id] = group
    return cap_id


def workspace_python_index(scans: list[RepoScan]) -> dict[str, dict[str, tuple[str, str]]]:
    """Per repo: module -> (repo, path) for modules other repos can import.

    Only package-rooted modules whose top-level name lives in exactly one repo
    are importable across repos; anything else would be a guess.
    """
    per_repo = {scan.name: python_module_index([p for p in scan.files if p.endswith(".py")]) for scan in scans}
    owners: dict[str, set[str]] = {}
    for repo, index in per_repo.items():
        for module, path in index.items():
            if "." in module or path.endswith("/__init__.py"):
                owners.setdefault(module.split(".")[0], set()).add(repo)
    shared = {}
    for repo, index in per_repo.items():
        for module, path in index.items():
            if owners.get(module.split(".")[0]) == {repo}:
                shared[module] = (repo, path)
    return {"shared": shared, **{repo: index for repo, index in per_repo.items()}}


def workspace_packages(scans: list[RepoScan]) -> dict[str, tuple[str, str]]:
    packages = {}
    for scan in scans:
        for manifest in tracked_files(scan.root):
            if manifest.rsplit("/", 1)[-1] == "package.json" and not NON_SOURCE.search(manifest):
                name = _package_name(scan.root / manifest)
                if name:
                    packages[name] = (scan.name, manifest.rsplit("/", 1)[0] if "/" in manifest else ".")
    return packages


def _package_name(manifest: Path) -> str | None:
    try:
        return json.loads(manifest.read_text()).get("name")
    except (OSError, ValueError):
        return None


def package_capability(scan: RepoScan, directory: str) -> str | None:
    prefix = "" if directory == "." else directory + "/"
    owners = Counter(cap for path, (cap, _, _) in scan.files.items() if path.startswith(prefix))
    return owners.most_common(1)[0][0] if owners else None


def add_import_edges(edges: EdgeSet, scans: list[RepoScan]) -> None:
    by_name = {scan.name: scan for scan in scans}
    modules = workspace_python_index(scans)
    packages = workspace_packages(scans)
    for scan in scans:
        for path, (cap_id, language, _) in scan.files.items():
            if language in ("Python", CONTRACT_LANGUAGE):
                _add_python_imports(edges, scan, path, cap_id, read_source(scan.root / path), modules, by_name)
            elif language in SCRIPT_LANGUAGES:
                _add_script_imports(edges, scan, path, cap_id, read_source(scan.root / path), packages, by_name)


def _add_python_imports(edges, scan, path, cap_id, source, modules, by_name) -> None:
    own = modules[scan.name]
    for module, names, line in python_imports(source):
        if resolve_python_import(module, names, own) is not None:
            continue
        target = resolve_python_import(module, names, modules["shared"])
        if target is not None:
            target_cap = by_name[target[0]].files[target[1]][0]
            edges.add(cap_id, target_cap, "import", f"{scan.name}/{path}:L{line} import {module}")


def _add_script_imports(edges, scan, path, cap_id, source, packages, by_name) -> None:
    for spec, line in script_imports(source):
        location = resolve_package(spec, packages)
        if location is None or location[0] == scan.name:
            continue
        target_cap = package_capability(by_name[location[0]], location[1])
        if target_cap:
            edges.add(cap_id, target_cap, "import", f"{scan.name}/{path}:L{line} import {spec}")


def _repo_of(capability_id: str) -> str:
    return capability_id.split("/", 1)[0]


def add_link_edges(edges: EdgeSet, links) -> None:
    for link in links:
        edges.add(link.source, link.target, link.kind, link.label)


def load_graph(repo_root: Path) -> dict:
    graph_path = repo_root / "graphify-out/graph.json"
    if not graph_path.exists():
        return {}
    return json.loads(graph_path.read_text())


# --- output -----------------------------------------------------------------


def capability_records(scan: RepoScan) -> list[dict]:
    files_by_cap: dict[str, list] = {}
    for path, (cap_id, language, loc) in sorted(scan.files.items()):
        files_by_cap.setdefault(cap_id, []).append([path, language, loc])
    records = [_curated_record(cap, files_by_cap.get(cap.id, [])) for cap in scan.spec.capabilities]
    records += [_fallback_record(scan.name, cap_id, group, files_by_cap[cap_id]) for cap_id, group in sorted(scan.fallback.items())]
    return [record for record in records if record["files"] or record["languages"]]


def _curated_record(cap: Capability, files: list) -> dict:
    record = _record(cap.id, cap.repo, cap.name, cap.summary, list(cap.tags), files)
    if not files:
        record["languages"] = [[language, None] for language in cap.languages]
    return record


def _fallback_record(repo: str, cap_id: str, group: str, files: list) -> dict:
    name = "Repo root" if group == "." else group
    return _record(cap_id, repo, name, "Not yet described in capabilities.json.", ["unmapped"], files)


def _record(cap_id: str, repo: str, name: str, summary: str, tags: list, files: list) -> dict:
    languages = Counter()
    for _, language, loc in files:
        languages[language] += loc
    return {
        "id": cap_id,
        "repo": repo,
        "name": name,
        "summary": summary,
        "tags": tags,
        "languages": [[language, loc] for language, loc in languages.most_common()],
        "loc": sum(languages.values()),
        "files": files,
    }


def repo_record(scan: RepoScan, manifest_entry: dict) -> dict:
    capabilities = capability_records(scan)
    return {
        "name": scan.name,
        "summary": scan.spec.summary,
        "external": scan.spec.external,
        "category": "external" if scan.spec.external else manifest_entry.get("category", "uncategorized"),
        "tier": manifest_entry.get("tier", ""),
        "loc": sum(cap["loc"] for cap in capabilities),
        "capabilities": capabilities,
    }


def coverage_lines(scan: RepoScan) -> str:
    total = sum(loc for _, _, loc in scan.files.values())
    if not total:
        return f"{scan.name:28} no source files"
    unmapped = sum(loc for cap, _, loc in scan.files.values() if cap in scan.fallback)
    empty = [cap.id for cap in scan.spec.capabilities if not any(f[0] == cap.id for f in scan.files.values())]
    line = f"{scan.name:28} {100 * (total - unmapped) / total:5.1f}% mapped of {total} loc"
    return line + (f"  (no files: {', '.join(empty)})" if empty else "")


def build_map(workspace_root: Path, taxonomy: Taxonomy, manifest: dict, exclude=frozenset()) -> tuple[dict, list[RepoScan]]:
    """Scan the workspace into map data. Repos in `exclude` (e.g. private ones on a
    public site) are left out entirely, together with every link that touches them."""
    entries = {repo["name"]: repo for repo in manifest.get("repos", [])}
    names = [name for name in dict.fromkeys([*taxonomy.repos, *sorted(entries)]) if not name.startswith(".") and name not in exclude]
    scans = []
    for name in names:
        spec = taxonomy.repos.get(name) or RepoSpec(name=name, summary="", external=False, capabilities=())
        root = workspace_root / name
        if spec.external:
            scans.append(RepoScan(name=name, root=root, spec=spec, files={}, fallback={}))
        elif (root / ".git").exists():
            scans.append(scan_repo(name, root, spec))
    code_scans = [scan for scan in scans if not scan.spec.external]
    edges = EdgeSet()
    for scan in code_scans:
        add_graph_edges(edges, load_graph(scan.root), {path: info[0] for path, info in scan.files.items()})
    add_import_edges(edges, code_scans)
    add_link_edges(edges, [link for link in taxonomy.links if not {_repo_of(link.source), _repo_of(link.target)} & set(exclude)])
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tags": list(taxonomy.tags),
        "repos": [repo_record(scan, entries.get(scan.name, {})) for scan in scans],
        "edges": edges.as_list(),
    }
    return data, scans


def viewer_assets() -> dict[str, str]:
    """Inline text for each placeholder of the viewer template, except the data."""
    vendor_js = "\n;\n".join((VENDOR_DIR / name).read_text() for name in VENDOR_SCRIPTS)
    vendor_css = "\n".join((VENDOR_DIR / name).read_text() for name in VENDOR_STYLES)
    return {LAYOUT_PLACEHOLDER: DEFAULT_LAYOUT.read_text(), VENDOR_JS_PLACEHOLDER: vendor_js, VENDOR_CSS_PLACEHOLDER: vendor_css}


# --- drift check -------------------------------------------------------------


def repo_findings(scan: RepoScan, curated: bool, head: str, graph_commit: str | None, track_graph: bool = True) -> list[str]:
    """What has drifted in one repo since the taxonomy and its graph were last updated.

    A repo listed with an empty capability list is auto-grouped on purpose, so
    its unmapped files are not a finding. Repos the workspace manifest marks
    exempt are skipped by the graph refresh helper, so their graph age is not
    tracked (`track_graph=False`).
    """
    name, found = scan.name, []
    if not curated:
        found.append(f"{name}: not listed in capabilities.json (add it, with capabilities or an empty list)")
    used = {cap for cap, _, _ in scan.files.values()}
    found += [f"{name}: capability {cap.id} matches no files (fix its paths in capabilities.json)"
              for cap in scan.spec.capabilities if cap.id not in used]
    unmapped = sorted(path for path, (cap, _, _) in scan.files.items() if cap in scan.fallback)
    if scan.spec.capabilities and unmapped:
        found.append(f"{name}: {len(unmapped)} source files in no capability, e.g. {unmapped[0]} (add them to a capability's paths)")
    if not track_graph:
        return found
    if graph_commit is None:
        found.append(f"{name}: no graphify graph (run graphify for this repo)")
    elif graph_commit != head:
        found.append(f"{name}: graphify graph was built at {graph_commit}, the repo is at {head} (refresh the graph)")
    return found


def head_commit(repo_root: Path) -> str:
    result = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "HEAD"], capture_output=True, text=True)
    return result.stdout.strip()


def graph_commit(repo_root: Path) -> str | None:
    graph = load_graph(repo_root)
    return graph.get("built_at_commit", "unknown") if graph else None


def check(scans: list[RepoScan], taxonomy: Taxonomy, manifest: dict) -> int:
    exempt = {repo["name"] for repo in manifest.get("repos", []) if repo.get("tier") == "exempt"}
    findings = [
        line
        for scan in scans
        if not scan.spec.external
        for line in repo_findings(scan, scan.name in taxonomy.repos, head_commit(scan.root), graph_commit(scan.root),
                                  track_graph=scan.name not in exempt)
    ]
    for line in findings:
        print(line)
    print(f"{len(findings)} findings" if findings else "No findings: the map is up to date.")
    return 1 if findings else 0


def render_html(template: str, data: dict, assets: dict[str, str]) -> str:
    html = template
    for placeholder, text in assets.items():
        html = html.replace(placeholder, text.replace("</script", "<\\/script"))
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    return html.replace(DATA_PLACEHOLDER, payload)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace-root", type=Path, default=REPO_ROOT.parent,
                        help="folder holding the sibling repos (default: the parent of this repo)")
    parser.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    parser.add_argument("--manifest", type=Path, default=None,
                        help=f"repo manifest (default: <workspace-root>/{MANIFEST_IN_WORKSPACE})")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "out")
    parser.add_argument("--exclude", action="append", default=[], metavar="REPO",
                        help="leave this repo out of the map, with all its links (repeatable)")
    parser.add_argument("--check", action="store_true",
                        help="report what drifted (unmapped files, empty capabilities, missing repos, stale graphs); writes nothing")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    taxonomy = parse_taxonomy(json.loads(args.taxonomy.read_text()))
    manifest = json.loads((args.manifest or args.workspace_root / MANIFEST_IN_WORKSPACE).read_text())
    data, scans = build_map(args.workspace_root.resolve(), taxonomy, manifest, exclude=frozenset(args.exclude))
    if args.check:
        return check(scans, taxonomy, manifest)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "code-map.json").write_text(json.dumps(data))
    (args.out / "code-map.html").write_text(render_html(DEFAULT_VIEWER.read_text(), data, viewer_assets()))
    for scan in scans:
        if not scan.spec.external:
            print(coverage_lines(scan))
    print(f"{len(data['edges'])} edges -> {args.out / 'code-map.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
