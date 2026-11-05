#!/usr/bin/env bash
# Builds the spike's toy repo in a new directory with two commits:
#   base  - shipping (guarded by rule SHIP-003), pricing, cart
#   agent - an "agent" change: discount() edited, loyalty_points() added
# Usage: spike/make_fixture.sh /path/to/new/dir
set -euo pipefail
D="${1:?usage: make_fixture.sh DIR}"
mkdir -p "$D" && cd "$D"
git init -q -b main
git config user.email fixture@example.com && git config user.name fixture
mkdir -p src/shop tests/rules && : > src/shop/__init__.py

cat > src/shop/shipping.py <<'PY'
FREE_SHIPPING_MIN = 50
FLAT_RATE = 5


def quote(total):
    """Public seam: shipping cost for an order total."""
    if total >= FREE_SHIPPING_MIN:
        return 0
    return _rate(total)


def _rate(total):
    return FLAT_RATE
PY

cat > src/shop/pricing.py <<'PY'
def discount(total, member):
    if member and total > 100:
        return total * 0.9
    return total


def unchanged_helper(x):
    return x + 1
PY

cat > src/shop/cart.py <<'PY'
class Cart:
    def __init__(self):
        self.items = []

    def add(self, price, qty=1):
        if qty < 1:
            raise ValueError("qty")
        self.items.append(price * qty)

    @property
    def total(self):
        return sum(self.items)

    @staticmethod
    def empty():
        return Cart()


def outer(x):
    def inner(y):
        return y * 2
    return inner(x) + 1


TAX = 0.2 * 100
PY

cat > tests/conftest.py <<'PY'
def pytest_configure(config):
    config.addinivalue_line("markers", "rule(*ids): links a test to rules in rules.toml")
PY

cat > tests/rules/test_shipping_rules.py <<'PY'
import pytest
from shop.shipping import quote

pytestmark = pytest.mark.rule("SHIP-003")


def test_well_over_threshold_ships_free():
    assert quote(80) == 0


def test_small_order_pays():
    assert quote(10) == 5
PY

cat > tests/test_pricing.py <<'PY'
from shop.pricing import discount, unchanged_helper


def test_member_discount():
    assert discount(200, True) == 180


def test_helper_runs():
    unchanged_helper(1)
PY

cat > tests/test_cart.py <<'PY'
from shop.cart import Cart, outer


def test_cart():
    c = Cart()
    c.add(10, 2)
    assert c.total == 20


def test_outer():
    assert outer(1) == 3
PY

cat > pyproject.toml <<'TOML'
[project]
name = "shop"
version = "0.0.0"

[tool.mutmut]
source_paths = ["src/shop"]
pytest_add_cli_args_test_selection = ["tests/"]

[tool.pytest.ini_options]
pythonpath = ["src"]
TOML

mkdir -p .mutineer
cat > .mutineer/rules.toml <<'TOML'
version = 1

[[rule]]
id = "SHIP-003"
statement = "Orders of $50 or more ship free"
seam = "shop.shipping.quote"
TOML

printf 'mutants/\n.venv/\n__pycache__/\n' > .gitignore
git add -A && git commit -qm base

git checkout -qb agent
python3 - <<'PY'
p = "src/shop/pricing.py"
s = open(p).read()
s = s.replace("        return total * 0.9\n", "        return round(total * 0.9, 2)\n")
s += "\n\ndef loyalty_points(total):\n    if total <= 0:\n        return 0\n    return int(total // 10)\n"
open(p, "w").write(s)
PY
cat >> tests/test_pricing.py <<'PY'


from shop.pricing import loyalty_points


def test_points():
    assert loyalty_points(55) == 5
PY
git add -A && git commit -qm "agent change"

# agent-gap: quote() gains an express option; rule tests unchanged, so SHIP-003 tests miss new mutants
git checkout -q main && git checkout -qb agent-gap
cat > src/shop/shipping.py <<'PY'
FREE_SHIPPING_MIN = 50
FLAT_RATE = 5
EXPRESS_FEE = 10


def quote(total, express=False):
    """Public seam: shipping cost for an order total."""
    if total >= FREE_SHIPPING_MIN:
        return 0
    rate = _rate(total)
    if express:
        rate += EXPRESS_FEE
    return rate


def _rate(total):
    return FLAT_RATE
PY
cat > tests/test_express.py <<'PY'
from shop.shipping import quote


def test_express_adds_fee():
    assert quote(10, express=True) == 15
PY
git add -A && git commit -qm "agent: express shipping"

# agent-tamper: agent-gap plus a weakened SHIP-003 test (== became >=)
git checkout -qb agent-tamper
sed -i.bak 's/assert quote(10) == 5/assert quote(10) >= 0/' tests/rules/test_shipping_rules.py && rm tests/rules/test_shipping_rules.py.bak
git add -A && git commit -qm "agent: loosen shipping test"

# agent-tamper-approved: same weakening, but SHIP-003 is edited in rules.toml in the same change
git checkout -qb agent-tamper-approved
sed -i.bak 's/statement = "Orders of \$50 or more ship free"/statement = "Orders of $50 or more ship free; smaller orders pay a non-negative fee"/' .mutineer/rules.toml && rm .mutineer/rules.toml.bak
git add -A && git commit -qm "rules: relax SHIP-003 (human approved)"

git checkout -q main
echo "fixture ready in $D (branches: main, agent, agent-gap, agent-tamper, agent-tamper-approved)"
