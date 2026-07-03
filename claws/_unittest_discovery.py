from __future__ import annotations

from contextlib import contextmanager
import importlib.util
from pathlib import Path
import sys
import unittest


LOCAL_MODULES = (
    "claw",
    "constants",
    "intent",
    "matcher",
    "prompts",
    "provider",
    "tools",
)
MISSING = object()


@contextmanager
def local_module_scope(package_dir):
    original_path = list(sys.path)
    saved_modules = {
        name: sys.modules.get(name, MISSING)
        for name in LOCAL_MODULES
    }
    for name in LOCAL_MODULES:
        sys.modules.pop(name, None)

    sys.path.insert(0, str(package_dir))
    try:
        yield
    finally:
        sys.path[:] = original_path
        for name, module in saved_modules.items():
            if module is MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


class LocalModuleSuite(unittest.TestSuite):
    def __init__(self, tests, package_dir):
        super().__init__(tests)
        self.package_dir = package_dir

    def run(self, result, debug=False):
        with local_module_scope(self.package_dir):
            return super().run(result, debug=debug)


def _test_files(package_dir, pattern):
    active_pattern = pattern or "test*.py"
    return sorted(Path(package_dir).glob(active_pattern))


def _load_test_module(package_name, test_path):
    module_name = f"_claws_{package_name.replace('-', '_')}_{test_path.stem}"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, test_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load {test_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_local_tests(loader, package_file, pattern):
    package_dir = Path(package_file).resolve().parent
    package_name = package_dir.name
    suite = unittest.TestSuite()
    with local_module_scope(package_dir):
        for test_path in _test_files(package_dir, pattern):
            module = _load_test_module(package_name, test_path)
            suite.addTests(loader.loadTestsFromModule(module))

    return LocalModuleSuite(suite, package_dir)
