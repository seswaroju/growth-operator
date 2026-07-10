"""MVP-001 acceptance tests — see docs/tickets/MVP-001.md."""

import importlib
import pkgutil
import subprocess

import core


def _core_module_names() -> list[str]:
    names = []
    for _finder, name, _ispkg in pkgutil.walk_packages(core.__path__, prefix="core."):
        names.append(name)
    return names


def test_every_core_module_imports_clean() -> None:
    modules = _core_module_names()
    assert modules, "expected at least one core submodule"
    for name in modules:
        importlib.import_module(name)


def test_makefile_targets_exist() -> None:
    for target in ["dev", "migrate", "test", "seed"]:
        result = subprocess.run(
            ["make", "-n", target],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"make -n {target} failed:\n{result.stderr}"
