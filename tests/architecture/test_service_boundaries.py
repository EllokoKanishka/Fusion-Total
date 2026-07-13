from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SERVICES = ROOT / "fusion_reader_v2" / "services"


class ServiceBoundaryTests(unittest.TestCase):
    def test_services_do_not_depend_on_facade_or_compatibility_module(self) -> None:
        failures: list[str] = []
        for path in sorted(SERVICES.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in {
                    "fusion_reader_v2.facade",
                    "fusion_reader_v2.service",
                }:
                    failures.append(f"{path.name}:{node.lineno} imports {node.module}")
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "__init__":
                    names = {
                        argument.arg for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
                    }
                    if "owner" in names:
                        failures.append(f"{path.name}:{node.lineno} accepts monolithic owner")
            if "FusionReaderV2" in {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}:
                failures.append(f"{path.name} references concrete FusionReaderV2")
        self.assertEqual(failures, [])

    def test_service_import_graph_has_no_cycles(self) -> None:
        modules = {path.stem for path in SERVICES.glob("*.py") if path.name != "__init__.py"}
        graph: dict[str, set[str]] = {module: set() for module in modules}
        for module in modules:
            tree = ast.parse((SERVICES / f"{module}.py").read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.module
                    and node.module.startswith("fusion_reader_v2.services.")
                ):
                    dependency = node.module.rsplit(".", 1)[-1]
                    if dependency in modules:
                        graph[module].add(dependency)

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(module: str) -> None:
            if module in visiting:
                self.fail(f"service import cycle detected at {module}")
            if module in visited:
                return
            visiting.add(module)
            for dependency in graph[module]:
                visit(dependency)
            visiting.remove(module)
            visited.add(module)

        for module in sorted(modules):
            visit(module)

    def test_public_imports_resolve_to_the_real_facade(self) -> None:
        from fusion_reader_v2 import FusionReaderV2 as package_class
        from fusion_reader_v2.facade import FusionReaderV2 as facade_class
        from fusion_reader_v2.service import FusionReaderV2 as compatibility_class

        self.assertIs(package_class, facade_class)
        self.assertIs(compatibility_class, facade_class)


if __name__ == "__main__":
    unittest.main()
