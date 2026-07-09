"""Minimal test runner for environments without pytest installed."""

from __future__ import annotations

import importlib.util
import inspect
import sys
import tempfile
from pathlib import Path


class TmpPathFactory:
    def __init__(self) -> None:
        self._temporary_directories = []

    def make(self) -> Path:
        temporary_directory = tempfile.TemporaryDirectory()
        self._temporary_directories.append(temporary_directory)
        return Path(temporary_directory.name)

    def cleanup(self) -> None:
        for temporary_directory in self._temporary_directories:
            temporary_directory.cleanup()


def main() -> int:
    root = Path(__file__).parent
    tests = sorted((root / "tests").glob("test_*.py"))
    failures = []
    test_count = 0

    for test_file in tests:
        module_name = f"_hbw_{test_file.stem}"
        spec = importlib.util.spec_from_file_location(module_name, test_file)
        if spec is None or spec.loader is None:
            failures.append((str(test_file), "unable to load module"))
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        for name, function in inspect.getmembers(module, inspect.isfunction):
            if not name.startswith("test_"):
                continue
            test_count += 1
            tmp_path_factory = TmpPathFactory()
            try:
                parameters = inspect.signature(function).parameters
                kwargs = {}
                if "tmp_path" in parameters:
                    kwargs["tmp_path"] = tmp_path_factory.make()
                function(**kwargs)
                print(f"PASS {test_file.name}::{name}")
            except Exception as exc:  # pragma: no cover - diagnostic runner
                failures.append((f"{test_file.name}::{name}", repr(exc)))
            finally:
                tmp_path_factory.cleanup()

    if failures:
        for test_name, failure in failures:
            print(f"FAIL {test_name}: {failure}")
        return 1
    print(f"{test_count} tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
