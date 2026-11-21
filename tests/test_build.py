from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


spec = importlib.util.spec_from_file_location(
    "build",
    # absolute(), not resolve(): mutation testing runs from a symlink overlay.
    Path(__file__).absolute().parents[1] / "build.py",
)
code_map = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = code_map
spec.loader.exec_module(code_map)


def taxonomy(repos=None, links=None):
    return code_map.parse_taxonomy(
        {
            "tags": ["execution", "storage", "rpc"],
            "repos": repos
            or {
                "core": {
                    "summary": "Core node",
                    "capabilities": [
                        {
                            "id": "exec",
                            "name": "Execution",
                            "tags": ["execution"],
                            "summary": "Runs transactions.",
                            "purpose": "Every state change goes through it.",
                            "paths": ["src/core/exec/*"],
                        },
                        {
                            "id": "store",
                            "name": "Storage",
                            "tags": ["storage"],
                            "paths": ["src/core/storage/*", "src/core/db.py"],
                        },
                    ],
                },
                "chain": {
                    "summary": "Consensus engine",
                    "external": True,
                    "capabilities": [
                        {
                            "id": "rpc",
                            "name": "RPC",
                            "tags": ["rpc"],
                            "languages": ["Go"],
                        }
                    ],
                },
            },
            "links": links if links is not None
            else [{"from": "chain/rpc", "to": "core/exec", "kind": "abci", "label": "ABCI"}],
        }
    )


class SourceFileTest(unittest.TestCase):
    def test_language_comes_from_extension(self):
        self.assertEqual(code_map.language_of("a/b.py"), "Python")
        self.assertEqual(code_map.language_of("web/App.tsx"), "TypeScript")
        self.assertEqual(code_map.language_of("vm/src/lib.rs"), "Rust")
        self.assertIsNone(code_map.language_of("README.md"))
        self.assertIsNone(code_map.language_of("release.v2/Makefile"))

    def test_contract_capability_reports_contract_language_for_python(self):
        self.assertEqual(code_map.language_of("contracts/currency.s.py", contract=True), "Xian contract")
        self.assertEqual(code_map.language_of("scripts/deploy.sh", contract=True), "Shell")

    def test_tests_vendor_and_docs_are_not_source(self):
        self.assertTrue(code_map.is_source("src/xian/processor.py"))
        for path in (
            "tests/unit/test_processor.py",
            "src/lib/__tests__/rpc.test.ts",
            "packages/vm/vendor/lmdb/mdb.c",
            "web/src/app.spec.ts",
            "docs/conf.py",
            "src/xian/test_helpers.py",
            "conftest.py",
            "pkg/conftest.py",
            "README.md",
        ):
            self.assertFalse(code_map.is_source(path), path)


class TaxonomyTest(unittest.TestCase):
    def test_capabilities_get_repo_qualified_ids(self):
        tax = taxonomy()
        self.assertEqual([c.id for c in tax.repos["core"].capabilities], ["core/exec", "core/store"])
        self.assertTrue(tax.repos["chain"].external)

    def test_unknown_tag_is_rejected(self):
        with self.assertRaisesRegex(code_map.TaxonomyError, "unknown tag 'magic'"):
            taxonomy(repos={"core": {"capabilities": [{"id": "x", "name": "X", "tags": ["magic"], "paths": ["*"]}]}}, links=[])

    def test_duplicate_capability_id_is_rejected(self):
        cap = {"id": "x", "name": "X", "tags": [], "paths": ["*"]}
        with self.assertRaisesRegex(code_map.TaxonomyError, "duplicate capability 'core/x'"):
            taxonomy(repos={"core": {"capabilities": [cap, cap]}}, links=[])

    def test_link_to_unknown_capability_is_rejected(self):
        with self.assertRaisesRegex(code_map.TaxonomyError, "unknown capability 'core/nope'"):
            taxonomy(links=[{"from": "chain/rpc", "to": "core/nope", "kind": "rpc"}])

    def test_capability_texts_and_an_external_project_url_are_read(self):
        tax = taxonomy(repos={"chain": {"external": True, "url": "https://github.com/acme/chain", "capabilities": [
            {"id": "rpc", "name": "RPC", "tags": [], "languages": ["Go"], "summary": "What.", "purpose": "Why."}]}}, links=[])
        cap = tax.repos["chain"].capabilities[0]
        self.assertEqual((cap.summary, cap.purpose, tax.repos["chain"].url), ("What.", "Why.", "https://github.com/acme/chain"))
        self.assertEqual(taxonomy().repos["core"].capabilities[1].purpose, "")
        self.assertEqual(taxonomy().repos["core"].url, "")

    def test_an_external_project_url_has_to_be_https(self):
        with self.assertRaisesRegex(code_map.TaxonomyError, "chain: url must start with https://"):
            taxonomy(repos={"chain": {"external": True, "url": "javascript:alert(1)", "capabilities": []}}, links=[])

    def test_capabilities_without_both_texts_are_findings(self):
        tax = taxonomy()
        self.assertEqual(code_map.description_findings(tax), [
            "core: capability core/store has no summary or purpose (describe it in capabilities.json)",
            "chain: capability chain/rpc has no summary or purpose (describe it in capabilities.json)",
        ])
        tax = taxonomy(repos={"core": {"capabilities": [
            {"id": "a", "name": "A", "tags": [], "paths": ["*"], "summary": "What."},
            {"id": "b", "name": "B", "tags": [], "paths": ["*"], "purpose": "Why."},
            {"id": "c", "name": "C", "tags": [], "paths": ["*"], "summary": "What.", "purpose": "Why."}]}}, links=[])
        self.assertEqual([line.split()[2] for line in code_map.description_findings(tax)], ["core/a", "core/b"])

    def test_external_capability_needs_languages_and_code_capability_needs_paths(self):
        with self.assertRaisesRegex(code_map.TaxonomyError, "chain/rpc needs languages"):
            taxonomy(repos={"chain": {"external": True, "capabilities": [{"id": "rpc", "name": "RPC", "tags": []}]}}, links=[])
        with self.assertRaisesRegex(code_map.TaxonomyError, "core/x needs paths"):
            taxonomy(repos={"core": {"capabilities": [{"id": "x", "name": "X", "tags": []}]}}, links=[])


class AssignmentTest(unittest.TestCase):
    def test_first_matching_capability_wins(self):
        caps = taxonomy().repos["core"].capabilities
        self.assertEqual(code_map.assign_capability("src/core/storage/lmdb.py", caps).id, "core/store")
        self.assertEqual(code_map.assign_capability("src/core/db.py", caps).id, "core/store")
        self.assertEqual(code_map.assign_capability("src/core/exec/deep/run.py", caps).id, "core/exec")
        self.assertIsNone(code_map.assign_capability("src/core/other.py", caps))

    def test_unmapped_files_group_by_leading_directories(self):
        self.assertEqual(code_map.fallback_group("src/pkg/mod/file.py"), "src/pkg")
        self.assertEqual(code_map.fallback_group("tool.py"), ".")


class PythonImportTest(unittest.TestCase):
    def test_module_names_start_at_the_topmost_package(self):
        files = ["src/contracting/__init__.py", "src/contracting/storage/__init__.py", "src/contracting/storage/driver.py", "plain.py",
                 "toppkg/__init__.py", "toppkg/sub.py"]
        index = code_map.python_module_index(files)
        self.assertEqual(index["toppkg.sub"], "toppkg/sub.py")
        self.assertEqual(index["contracting.storage.driver"], "src/contracting/storage/driver.py")
        self.assertEqual(index["contracting.storage"], "src/contracting/storage/__init__.py")
        self.assertEqual(index["plain"], "plain.py")

    def test_imports_are_parsed_with_line_numbers(self):
        source = "import os\nfrom contracting.storage import driver, orm\nfrom . import local\n"
        self.assertEqual(
            code_map.python_imports(source),
            [("os", (), 1), ("contracting.storage", ("driver", "orm"), 2)],
        )

    def test_unparseable_python_yields_no_imports(self):
        self.assertEqual(code_map.python_imports("def broken(:\n"), [])

    def test_resolution_prefers_imported_submodule_then_longest_prefix(self):
        index = {
            "contracting.storage": "src/contracting/storage/__init__.py",
            "contracting.storage.driver": "src/contracting/storage/driver.py",
            "contracting": "src/contracting/__init__.py",
        }
        resolve = code_map.resolve_python_imports
        self.assertEqual(resolve("contracting.storage", ("driver",), index), {"src/contracting/storage/driver.py"})
        self.assertEqual(resolve("contracting.storage.orm.Thing", (), index), {"src/contracting/storage/__init__.py"})
        self.assertEqual(resolve("contracting.missing", (), index), {"src/contracting/__init__.py"})
        self.assertEqual(resolve("requests", (), index), set())


class ScriptImportTest(unittest.TestCase):
    def test_import_specs_cover_static_dynamic_and_require(self):
        source = (
            'import { Client } from "@xian-tech/client";\n'
            "export * from './local';\n"
            'const vm = await import("@xian-tech/vm");\n'
            "const x = require('lodash');\n"
            'import "./side-effect.css";\n'
        )
        self.assertEqual(
            code_map.script_imports(source),
            [("@xian-tech/client", 1), ("./local", 2), ("@xian-tech/vm", 3), ("lodash", 4), ("./side-effect.css", 5)],
        )

    def test_line_numbers_count_leading_blank_lines(self):
        self.assertEqual(code_map.script_imports('\nimport "x";\n'), [("x", 2)])

    def test_package_specs_resolve_to_the_package_directory(self):
        packages = {"@xian-tech/client": ("xian-js", "packages/client")}
        resolve = code_map.resolve_package
        self.assertEqual(resolve("@xian-tech/client", packages), ("xian-js", "packages/client"))
        self.assertEqual(resolve("@xian-tech/client/watch", packages), ("xian-js", "packages/client"))
        self.assertIsNone(resolve("./local", packages))
        self.assertIsNone(resolve("@xian-tech/clientx", packages))


class GraphEdgeTest(unittest.TestCase):
    def test_graph_links_between_files_of_different_capabilities_are_counted(self):
        graph = {
            "nodes": [
                {"id": "a", "label": "run", "source_file": "src/core/exec/run.py", "source_location": "L3"},
                {"id": "b", "label": "get", "source_file": "src/core/storage/kv.py"},
                {"id": "c", "label": "helper", "source_file": "src/core/exec/util.py"},
                {"id": "d", "label": "Path"},
            ],
            "links": [
                {"source": "a", "target": "b", "relation": "calls", "source_file": "src/core/exec/run.py", "source_location": "L9"},
                {"source": "a", "target": "b", "relation": "calls"},
                {"source": "a", "target": "c", "relation": "calls"},
                {"source": "a", "target": "d", "relation": "uses"},
                {"source": "a", "target": "b", "relation": "contains"},
                {"source": "a", "target": "missing", "relation": "calls"},
            ],
        }
        file_caps = {"src/core/exec/run.py": "core/exec", "src/core/exec/util.py": "core/exec", "src/core/storage/kv.py": "core/store"}
        edges = code_map.EdgeSet()
        code_map.add_graph_edges(edges, graph, file_caps)
        [edge] = edges.as_list()
        self.assertEqual((edge["from"], edge["to"], edge["kind"], edge["weight"]), ("core/exec", "core/store", "code", 2))
        self.assertEqual(edge["examples"], ["src/core/exec/run.py:L9 run calls get", "src/core/exec/run.py:L3 run calls get"])


class EdgeSetTest(unittest.TestCase):
    def test_weights_accumulate_and_examples_are_capped(self):
        edges = code_map.EdgeSet(max_examples=2)
        for line in range(5):
            edges.add("a/x", "b/y", "import", f"f.py:L{line}")
        edges.add("a/x", "a/x", "import", "self loop is dropped")
        [edge] = edges.as_list()
        self.assertEqual(edge["weight"], 5)
        self.assertEqual(edge["examples"], ["f.py:L0", "f.py:L1"])


class GitHubUrlTest(unittest.TestCase):
    def test_github_remotes_become_plain_https_repo_urls(self):
        for remote in ("https://github.com/acme/lib.git", "https://github.com/acme/lib", "https://github.com/acme/lib/",
                       "git@github.com:acme/lib.git", "ssh://git@github.com/acme/lib.git",
                       "https://x-access-token:secret@github.com/acme/lib.git"):
            self.assertEqual(code_map.github_url(remote), "https://github.com/acme/lib", remote)
        self.assertEqual(code_map.github_url("https://github.com/Acme-1/my.repo_x.git"), "https://github.com/Acme-1/my.repo_x")

    def test_anything_else_gets_no_link(self):
        for remote in ("", "https://gitlab.com/acme/lib.git", "https://github.com.evil.io/acme/lib", "https://github.com/acme",
                       "https://github.com/acme/lib/tree/main", "/srv/git/lib.git", "https://github.com/acme/lib?x=1"):
            self.assertIsNone(code_map.github_url(remote), remote)


class CapabilityPathTest(unittest.TestCase):
    def test_a_capability_points_at_the_folder_its_files_share_or_its_only_file(self):
        self.assertEqual(code_map.capability_path(["src/a/x.py", "src/a/b/y.py"]), "src/a")
        self.assertEqual(code_map.capability_path(["src/a/x.py", "src/b/y.py"]), "src")
        self.assertEqual(code_map.capability_path(["src/a/x.py"]), "src/a/x.py")
        self.assertEqual(code_map.capability_path(["x.py", "src/y.py"]), "")
        self.assertEqual(code_map.capability_path([]), "")


class WorkspaceBuildTest(unittest.TestCase):
    """Builds the map for a two-repo workspace on disk, the way the CLI does."""

    # The page is megabytes; report a missing or leftover snippet without printing it.
    def assertPageHas(self, html, snippet):
        self.assertTrue(snippet in html, f"{snippet!r} missing from the page")

    def assertPageLacks(self, html, snippet):
        self.assertFalse(snippet in html, f"{snippet!r} left in the page")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self._repo("lib", {
            "src/libpkg/__init__.py": "",
            "src/libpkg/core.py": "def f():\n    return 1\n",
            "js/package.json": json.dumps({"name": "@x/lib"}),
            "js/index.ts": "export const a = 1;\n",
            "src/libpkg/meta.json": json.dumps({"name": "@x/ghost"}),
        })
        self._repo("misc", {"tool.py": "print(1)\n"})
        self._repo("app", {
            "app/main.py": "from libpkg.core import f\nimport requests\nimport helpers\n",
            "app/helpers.py": "",
            "app/extra/tool.py": "def run():\n    pass\n",
            "web/main.ts": 'import { a } from "@x/lib";\nimport "./local";\nimport { b } from "@x/app";\nimport "@x/ghost";\n',
            "package.json": json.dumps({"name": "@x/app"}),
            "tests/test_main.py": "from libpkg.core import f\n",
            "graphify-out/graph.json": json.dumps({
                "nodes": [
                    {"id": "m", "label": "main", "source_file": "app/main.py"},
                    {"id": "t", "label": "run", "source_file": "app/extra/tool.py"},
                ],
                "links": [{"source": "m", "target": "t", "relation": "calls", "source_location": "L2"}],
            }),
        })
        self.taxonomy_raw = {
            "tags": ["rpc", "sdk", "unmapped"],
            "repos": {
                "chain": {"external": True, "capabilities": [{"id": "rpc", "name": "RPC", "tags": ["rpc"], "languages": ["Go"]}]},
                "lib": {"summary": "Library", "capabilities": [
                    {"id": "core", "name": "Core", "tags": ["sdk"], "paths": ["src/*"]},
                    {"id": "js", "name": "JS", "tags": ["sdk"], "paths": ["js/*"]},
                ]},
                "app": {"capabilities": [
                    {"id": "main", "name": "Main", "tags": [], "paths": ["app/main.py", "app/helpers.py"]},
                    {"id": "web", "name": "Web", "tags": [], "paths": ["web/*"]},
                ]},
            },
            "links": [{"from": "app/main", "to": "chain/rpc", "kind": "rpc", "label": "JSON-RPC"}],
        }
        self.manifest = {"repos": [{"name": "app", "category": "apps", "tier": "light"}, {"name": "misc", "category": "tools", "tier": "exempt"},
                                   {"name": ".github", "category": "org"}]}

    def _repo(self, name, files):
        repo = self.root / name
        for path, text in files.items():
            (repo / path).parent.mkdir(parents=True, exist_ok=True)
            (repo / path).write_text(text)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "fixture"], check=True)

    def build(self):
        data, _ = code_map.build_map(self.root, code_map.parse_taxonomy(self.taxonomy_raw), self.manifest)
        return data

    def test_repos_link_to_github_and_capabilities_carry_their_texts_and_folder(self):
        lib = str(self.root / "lib")
        git = lambda *args: subprocess.run(["git", "-C", lib, "-c", "user.name=t", "-c", "user.email=t@t", *args],
                                           check=True, capture_output=True, text=True).stdout.strip()
        subprocess.run(["git", "init", "-q", "--bare", str(self.root / "remote.git")], check=True)
        git("remote", "add", "origin", str(self.root / "remote.git"))
        git("push", "-q", "-u", "origin", "HEAD")
        pushed = git("rev-parse", "HEAD")
        git("remote", "set-url", "origin", "https://user:token@github.com/acme/lib.git")
        git("commit", "-q", "--allow-empty", "-m", "not pushed yet")  # GitHub has no such commit
        self.taxonomy_raw["repos"]["chain"]["url"] = "https://github.com/acme/chain"
        self.taxonomy_raw["repos"]["lib"]["capabilities"][0].update(summary="Core code.", purpose="Everything uses it.")
        repos = {repo["name"]: repo for repo in self.build()["repos"]}
        self.assertEqual((repos["lib"]["url"], repos["lib"]["commit"]), ("https://github.com/acme/lib", pushed))
        self.assertEqual((repos["app"]["url"], repos["app"]["commit"]), (None, None))
        self.assertEqual((repos["chain"]["url"], repos["chain"]["commit"]), ("https://github.com/acme/chain", None))
        caps = {cap["id"]: cap for repo in repos.values() for cap in repo["capabilities"]}
        self.assertEqual((caps["lib/core"]["summary"], caps["lib/core"]["purpose"], caps["lib/core"]["path"]),
                         ("Core code.", "Everything uses it.", "src/libpkg"))
        self.assertEqual((caps["app/web"]["path"], caps["app/main"]["path"], caps["misc/~."]["path"]), ("web/main.ts", "app", "tool.py"))
        self.assertEqual((caps["chain/rpc"]["purpose"], caps["chain/rpc"]["path"]), ("", ""))
        self.assertEqual(caps["app/~app/extra"]["purpose"], "")

    def test_edges_cover_graph_calls_cross_repo_imports_and_curated_links(self):
        edges = {(e["from"], e["to"], e["kind"]): e for e in self.build()["edges"]}
        self.assertEqual(
            set(edges),
            {
                ("app/main", "lib/core", "import"),
                ("app/web", "lib/js", "import"),
                ("app/main", "app/~app/extra", "code"),
                ("app/main", "chain/rpc", "rpc"),
            },
        )
        self.assertEqual(edges[("app/main", "lib/core", "import")]["examples"], ["app/app/main.py:L1 import libpkg.core"])
        self.assertEqual(edges[("app/main", "chain/rpc", "rpc")]["examples"], ["JSON-RPC"])

    def test_one_python_import_can_link_to_several_capabilities(self):
        # core is a submodule. The two exported names are defined in the package itself.
        (self.root / "lib/src/libpkg/__init__.py").write_text("one, two = 1, 2\n")
        self.taxonomy_raw["repos"]["lib"]["capabilities"].insert(0, {
            "id": "api", "name": "API", "paths": ["src/libpkg/__init__.py"],
        })
        (self.root / "app/app/main.py").write_text("from libpkg import core, one, two\n")
        imports = {e["to"]: e["weight"] for e in self.build()["edges"]
                   if e["kind"] == "import" and e["from"] == "app/main"}
        self.assertEqual(imports, {"lib/core": 1, "lib/api": 1})

    def test_links_only_reference_capabilities_present_in_the_map(self):
        shutil.rmtree(self.root / "lib")
        self.taxonomy_raw["repos"]["app"]["capabilities"][1]["paths"] = ["moved/*"]
        self.taxonomy_raw["links"].extend([
            {"from": "app/main", "to": "lib/core", "kind": "rpc"},
            {"from": "app/web", "to": "app/main", "kind": "rpc"},
        ])
        data = self.build()
        ids = {cap["id"] for repo in data["repos"] for cap in repo["capabilities"]}
        self.assertTrue(all({e["from"], e["to"]} <= ids for e in data["edges"]), data["edges"])
        self.assertIn(("app/main", "chain/rpc"), [(e["from"], e["to"]) for e in data["edges"]])

    def test_repos_list_capabilities_with_languages_and_unmapped_groups(self):
        repos = {repo["name"]: repo for repo in self.build()["repos"]}
        self.assertEqual(list(repos), ["chain", "lib", "app", "misc"])
        self.assertEqual([(c["id"], c["name"]) for c in repos["misc"]["capabilities"]], [("misc/~.", "Repo root")])
        app = {cap["id"]: cap for cap in repos["app"]["capabilities"]}
        self.assertEqual(list(app), ["app/main", "app/web", "app/~app/extra"])
        self.assertEqual(app["app/~app/extra"]["tags"], ["unmapped"])
        self.assertEqual(app["app/main"]["languages"], [["Python", 3]])
        self.assertEqual([f[0] for f in app["app/main"]["files"]], ["app/helpers.py", "app/main.py"])
        self.assertEqual((repos["app"]["category"], repos["app"]["tier"], repos["app"]["loc"]), ("apps", "light", 9))
        self.assertEqual(repos["chain"]["category"], "external")
        self.assertEqual(repos["chain"]["capabilities"][0]["languages"], [["Go", None]])

    def test_excluded_repos_leave_no_trace_in_the_data(self):
        data, _ = code_map.build_map(self.root, code_map.parse_taxonomy(self.taxonomy_raw), self.manifest, exclude={"lib", "chain"})
        self.assertEqual([repo["name"] for repo in data["repos"]], ["app", "misc"])
        self.assertEqual([(e["from"], e["to"], e["kind"]) for e in data["edges"]], [("app/main", "app/~app/extra", "code")])
        self.assertNotIn("lib", json.dumps(data))

    def test_external_systems_reached_only_through_excluded_repos_go_too(self):
        data, _ = code_map.build_map(self.root, code_map.parse_taxonomy(self.taxonomy_raw), self.manifest, exclude={"app"})
        self.assertEqual([repo["name"] for repo in data["repos"]], ["lib", "misc"])
        self.taxonomy_raw["repos"]["chain"]["capabilities"].append({"id": "idle", "name": "Idle", "tags": [], "languages": ["Go"]})
        self.taxonomy_raw["links"].append({"from": "lib/core", "to": "chain/idle", "kind": "rpc"})
        data, _ = code_map.build_map(self.root, code_map.parse_taxonomy(self.taxonomy_raw), self.manifest, exclude={"app"})
        self.assertEqual([repo["name"] for repo in data["repos"]], ["chain", "lib", "misc"])

    def test_cli_exclude_flag_drops_repos_from_the_page(self):
        taxonomy_path = self.root / "taxonomy.json"
        taxonomy_path.write_text(json.dumps(self.taxonomy_raw))
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text(json.dumps(self.manifest))
        out = self.root / "site"
        with contextlib.redirect_stdout(io.StringIO()):
            code_map.main(["--workspace-root", str(self.root), "--taxonomy", str(taxonomy_path), "--manifest", str(manifest_path),
                           "--out", str(out), "--exclude", "lib", "--exclude", "misc"])
        names = [repo["name"] for repo in json.loads((out / "code-map.json").read_text())["repos"]]
        self.assertEqual(names, ["chain", "app"])

    def test_pages_build_includes_only_verified_public_repositories(self):
        # lib is taxonomy-only, misc a linked worktree, and app a public repo with a different slug.
        (self.root / "misc").rename(self.root / "checkout")
        subprocess.run(["git", "-C", str(self.root / "checkout"), "worktree", "add", "-q", "--detach",
                        str(self.root / "misc")], check=True)
        self.manifest.update(organization="fixture")
        self.manifest["repos"][0]["slug"] = "public-app"
        (self.root / "xian-meta").mkdir()
        (self.root / "xian-meta/workspace-repos.json").write_text(json.dumps(self.manifest))
        self._repo("map", {"capabilities.json": json.dumps(self.taxonomy_raw)})
        repo = self.root / "map"
        for path in ("build.py", "viewer", "scripts"):
            (repo / path).symlink_to(code_map.REPO_ROOT / path)
        destination = self.root / "published.git"
        subprocess.run(["git", "init", "-q", "--bare", str(destination)], check=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(destination)], check=True)
        gh = self.root / "gh"
        gh.write_text('#!/bin/sh\nif [ "$3" = fixture/public-app ]; then echo PUBLIC; else echo PRIVATE; fi\n')
        gh.chmod(0o755)
        env = dict(os.environ, PATH=str(self.root) + os.pathsep + os.environ["PATH"],
                   GIT_AUTHOR_NAME="test", GIT_AUTHOR_EMAIL="test@example.org",
                   GIT_COMMITTER_NAME="test", GIT_COMMITTER_EMAIL="test@example.org")
        subprocess.run(["bash", str(repo / "scripts/publish-pages.sh")], check=True, env=env, capture_output=True)
        page = subprocess.check_output(["git", "-C", str(destination), "show", "gh-pages:index.html"], text=True)
        self.assertPageHas(page, '"id":"app/main"')
        self.assertPageHas(page, '"id":"chain/rpc"')
        self.assertPageLacks(page, '"id":"lib/core"')
        self.assertPageLacks(page, '"id":"misc/~."')

    def test_check_mode_reports_drift_and_writes_nothing(self):
        (self.root / "xian-meta").mkdir()
        (self.root / "xian-meta/workspace-repos.json").write_text(json.dumps(self.manifest))
        taxonomy_path = self.root / "taxonomy.json"
        taxonomy_path.write_text(json.dumps(self.taxonomy_raw))
        out = self.root / "out"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = code_map.main(["--workspace-root", str(self.root), "--taxonomy", str(taxonomy_path), "--out", str(out), "--check"])
        self.assertEqual(code, 1)
        self.assertFalse(out.exists())
        report = stdout.getvalue().splitlines()
        self.assertIn("app: 1 source files in no capability, e.g. app/extra/tool.py (add them to a capability's paths)", report)
        self.assertIn("misc: not listed in capabilities.json (add it, with capabilities or an empty list)", report)
        self.assertIn("lib: no graphify graph (run graphify for this repo)", report)
        self.assertTrue(any(line.startswith("app: graphify graph was built at unknown") for line in report), report)
        self.assertNotIn("misc: no graphify graph (run graphify for this repo)", report)
        self.assertIn("lib: capability lib/js has no summary or purpose (describe it in capabilities.json)", report)
        self.assertEqual(report[-1], "9 findings")

    def test_check_mode_passes_when_nothing_drifted(self):
        self.taxonomy_raw["repos"]["misc"] = {"capabilities": []}
        self.taxonomy_raw["repos"]["app"]["capabilities"].append({"id": "extra", "name": "Extra", "tags": [], "paths": ["app/extra/*"]})
        for repo in self.taxonomy_raw["repos"].values():
            for cap in repo["capabilities"]:
                cap.update(summary="What.", purpose="Why.")
        for repo in ("lib", "app", "misc"):
            head = subprocess.run(["git", "-C", str(self.root / repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
            graph = self.root / repo / "graphify-out/graph.json"
            graph.parent.mkdir(exist_ok=True)
            existing = json.loads(graph.read_text()) if graph.exists() else {}
            graph.write_text(json.dumps({**existing, "built_at_commit": head}))
        taxonomy_path = self.root / "taxonomy.json"
        taxonomy_path.write_text(json.dumps(self.taxonomy_raw))
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text(json.dumps(self.manifest))
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = code_map.main(["--workspace-root", str(self.root), "--taxonomy", str(taxonomy_path), "--manifest", str(manifest_path), "--check"])
        self.assertEqual((code, stdout.getvalue()), (0, "No findings: the map is up to date.\n"))

    def test_cli_writes_html_with_embedded_data_and_reports_coverage(self):
        taxonomy_path = self.root / "taxonomy.json"
        taxonomy_path.write_text(json.dumps(self.taxonomy_raw))
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text(json.dumps(self.manifest))
        out = self.root / "out/nested"
        args = ["--workspace-root", str(self.root), "--taxonomy", str(taxonomy_path), "--manifest", str(manifest_path), "--out", str(out)]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(code_map.main(args), 0)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(code_map.main(args), 0)  # rerunning over existing output is fine
        html = (out / "code-map.html").read_text()
        self.assertPageLacks(html, code_map.DATA_PLACEHOLDER)
        self.assertPageLacks(html, code_map.LAYOUT_PLACEHOLDER)
        self.assertPageLacks(html, code_map.VENDOR_JS_PLACEHOLDER)
        self.assertPageLacks(html, code_map.VENDOR_CSS_PLACEHOLDER)
        self.assertPageHas(html, "window.jsxRuntime")
        self.assertPageHas(html, ".ReactFlow={}")
        self.assertPageHas(html, ".react-flow__viewport")
        self.assertLess(html.index("window.jsxRuntime"), html.index(".ReactFlow={}"))
        self.assertPageHas(html, "g.ELK = f()")
        self.assertPageHas(html, "function elkRepoGraph")
        self.assertPageHas(html, '"id":"app/~app/extra"')
        self.assertPageHas(html, '<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,')
        self.assertEqual(json.loads((out / "code-map.json").read_text())["tags"], ["rpc", "sdk", "unmapped"])
        report = stdout.getvalue()
        self.assertIn("app                           77.8% mapped of 9 loc\n", report)
        self.assertIn("lib                          100.0% mapped of 3 loc\n", report)
        self.assertIn("4 edges", report)


class FindingsTest(unittest.TestCase):
    def scan(self, capabilities, files, fallback=None, name="core"):
        spec = code_map.RepoSpec(name=name, summary="", external=False, capabilities=capabilities)
        return code_map.RepoScan(name=name, root=Path("."), spec=spec, files=files, fallback=fallback or {})

    def test_an_up_to_date_repo_has_no_findings(self):
        caps = taxonomy().repos["core"].capabilities
        scan = self.scan(caps, {"src/core/exec/a.py": ("core/exec", "Python", 3), "src/core/db.py": ("core/store", "Python", 1)})
        self.assertEqual(code_map.repo_findings(scan, curated=True, head="abc", graph_commit="abc"), [])

    def test_findings_name_each_kind_of_drift(self):
        caps = taxonomy().repos["core"].capabilities
        scan = self.scan(caps, {"src/core/exec/a.py": ("core/exec", "Python", 3), "x/y.py": ("core/~x", "Python", 1),
                                "x/z.py": ("core/~x", "Python", 2)}, {"core/~x": "x"})
        self.assertEqual(code_map.repo_findings(scan, curated=True, head="abc", graph_commit="old"), [
            "core: capability core/store matches no files (fix its paths in capabilities.json)",
            "core: 2 source files in no capability, e.g. x/y.py (add them to a capability's paths)",
            "core: graphify graph was built at old, the repo is at abc (refresh the graph)",
        ])

    def test_missing_graph_and_missing_taxonomy_entry_are_findings(self):
        scan = self.scan((), {"tool.py": ("misc/~.", "Python", 1)}, {"misc/~.": "."}, name="misc")
        self.assertEqual(code_map.repo_findings(scan, curated=False, head="abc", graph_commit=None), [
            "misc: not listed in capabilities.json (add it, with capabilities or an empty list)",
            "misc: no graphify graph (run graphify for this repo)",
        ])

    def test_exempt_repos_are_not_held_to_graph_freshness(self):
        scan = self.scan((), {"a/b.py": ("big/~a", "Python", 9)}, {"big/~a": "a"}, name="big")
        self.assertEqual(code_map.repo_findings(scan, curated=True, head="abc", graph_commit="old", track_graph=False), [])

    def test_repos_listed_with_no_capabilities_are_auto_grouped_on_purpose(self):
        scan = self.scan((), {"a/b.py": ("big/~a", "Python", 9)}, {"big/~a": "a"}, name="big")
        self.assertEqual(code_map.repo_findings(scan, curated=True, head="abc", graph_commit="abc"), [])


class EmptyRepoReportTest(unittest.TestCase):
    def test_repo_without_source_files_says_so(self):
        spec = code_map.RepoSpec(name="docs", summary="", external=False, capabilities=())
        scan = code_map.RepoScan(name="docs", root=Path("."), spec=spec, files={}, fallback={})
        self.assertEqual(code_map.coverage_lines(scan), "docs                         no source files")


class InlineScriptTest(unittest.TestCase):
    def test_inlined_scripts_cannot_close_their_script_tag(self):
        html = code_map.render_html("<script>/*__CODE_MAP_LAYOUT__*/</script>", {}, {code_map.LAYOUT_PLACEHOLDER: 'x("</script>")'})
        self.assertEqual(html, '<script>x("<\\/script>")</script>')


class TrackedFilesTest(unittest.TestCase):
    def test_a_directory_that_is_not_a_git_repo_fails_loudly(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(subprocess.CalledProcessError):
                code_map.tracked_files(Path(temp) / "missing")


class CoverageReportTest(unittest.TestCase):
    def test_report_names_curated_capabilities_without_files(self):
        spec = code_map.RepoSpec(name="core", summary="", external=False, capabilities=taxonomy().repos["core"].capabilities)
        scan = code_map.RepoScan(name="core", root=Path("."), spec=spec,
                                 files={"src/core/exec/a.py": ("core/exec", "Python", 3), "x/y.py": ("core/~x", "Python", 1)},
                                 fallback={"core/~x": "x"})
        self.assertEqual(code_map.coverage_lines(scan), "core                          75.0% mapped of 4 loc  (no files: core/store)")


@unittest.skipUnless(shutil.which("node"), "node is needed for the viewer layout tests")
class ViewerLayoutTest(unittest.TestCase):
    def test_layout_module_passes_its_node_tests(self):
        root = Path(__file__).absolute().parents[1]
        result = subprocess.run(
            ["node", "--preserve-symlinks", "--preserve-symlinks-main", "--test", "tests/layout.test.cjs"],
            cwd=root, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
