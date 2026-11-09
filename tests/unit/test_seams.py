"""``seams.seam_problem``: a seam resolves statically, without importing user code"""

from __future__ import annotations

import textwrap

import pytest

from mutineer.seams import seam_problem

PRICING = """\
import os
from decimal import Decimal as D

RATE = 0.9
A, (B, C) = 1, (2, 3)

if os.environ.get("X"):
    LATE = 1

try:
    import json
except ImportError:
    FALLBACK = None


def discount(total):
    return total * RATE


class Cart:
    size: int = 0

    def add(self, x):
        pass

    class Inner:
        def deep(self):
            pass


raise SystemExit("seam_problem must never import this module")
"""


@pytest.fixture
def project(tmp_path):
    files = {
        "src/shop/__init__.py": "",
        "src/shop/pricing.py": PRICING,
        "lib/pkg/core.py": "def run():\n    pass\n",
        "toplevel.py": "VALUE = 1\n",
        "src/shop/broken.py": "def (:\n",
        "pyproject.toml": '[tool.mutmut]\nsource_paths = ["lib/pkg"]\n',
    }
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text))
    return tmp_path


@pytest.mark.parametrize(
    "seam",
    [
        "shop.pricing.discount",
        "shop.pricing.Cart.add",
        "shop.pricing.Cart.size",
        "shop.pricing.Cart.Inner.deep",
        "shop.pricing",
        "shop",
        "shop.pricing.RATE",
        "shop.pricing.C",
        "shop.pricing.D",
        "shop.pricing.os",
        "shop.pricing.LATE",
        "shop.pricing.json",
        "shop.pricing.FALLBACK",
        "pkg.core.run",
        "toplevel.VALUE",
    ],
)
def test_resolving_seams(project, seam):
    assert seam_problem(project, seam) is None


def test_missing_attribute_names_the_part(project):
    problem = seam_problem(project, "shop.pricing.nope")
    assert (
        problem == "seam shop.pricing.nope: 'nope' not found in shop.pricing (src/shop/pricing.py)"
    )


def test_missing_method_names_the_part(project):
    problem = seam_problem(project, "shop.pricing.Cart.remove")
    assert problem is not None
    assert "'remove' not found in shop.pricing.Cart" in problem


def test_missing_module_inside_a_package_names_the_part(project):
    problem = seam_problem(project, "shop.nothing.quote")
    assert problem is not None
    assert "'nothing' not found in shop" in problem


def test_missing_top_module(project):
    problem = seam_problem(project, "nowhere.quote")
    assert problem is not None
    assert "no module 'nowhere'" in problem


def test_attribute_of_a_function_doesnt_resolve(project):
    problem = seam_problem(project, "shop.pricing.discount.inner")
    assert problem is not None
    assert "'inner'" in problem


def test_unparsable_module(project):
    assert "can't be parsed" in (seam_problem(project, "shop.broken.x") or "")


def test_not_a_dotted_path(project):
    assert "must be a dotted path" in (seam_problem(project, "shop..pricing") or "")
