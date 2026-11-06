"""Builders for triage, verdict and reporter tests

- ``build``: makes ``FunctionRef``s, ``MutantResult``s, ``EngineRun``s and findings
  without running mutmut. Mutants are named ``<function>__mutmut_<n>``.
- ``ship_rules``: a rules file with SHIP-003 (seam ``shop.shipping.quote``).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pytest

from mutineer.models import (
    Audience,
    EngineRun,
    Finding,
    FunctionRef,
    MutantResult,
    MutantStatus,
    Rule,
    RulesFile,
    Severity,
    UnmeasuredLocation,
)

QUOTE = "shop.shipping.x_quote"
DISCOUNT = "shop.pricing.x_discount"
RULE_TEST = "tests/rules/test_shipping_rules.py::test_small_order_pays"
IMPL_TEST = "tests/test_express.py::test_express_adds_fee"
SHIP_RULE_MAP = {RULE_TEST: ["SHIP-003"]}


class Build:
    quote = QUOTE
    discount = DISCOUNT
    rule_test = RULE_TEST
    impl_test = IMPL_TEST
    rule_map = SHIP_RULE_MAP

    @staticmethod
    def fn(name: str, file: str = "src/shop/mod.py", start: int = 1, end: int = 20) -> FunctionRef:
        qualname = name.rsplit(".", 1)[-1].removeprefix("x_").replace("ǁ", ".").strip(".")
        return FunctionRef(
            mutmut_name=name, file=file, qualname=qualname, start_line=start, end_line=end
        )

    @staticmethod
    def mutant(
        fn: str,
        n: int,
        all: str | None,
        rule: str | None = None,
        line: int | None = None,
        text: tuple[str, str] | None = None,
    ) -> list[MutantResult]:
        """One mutant's results: ``all``/``rule`` are status values, None for no result.

        Like the engine, the text is only on records that weren't killed.
        """
        out = []
        for run, status in (("all", all), ("rule", rule)):
            if status is None:
                continue
            st = MutantStatus(status)
            shown = st not in (MutantStatus.KILLED, MutantStatus.TYPE_CHECK)
            original, mutated = text or (f"original {n}", f"mutated {n}")
            out.append(
                MutantResult(
                    name=f"{fn}__mutmut_{n}",
                    function=fn,
                    run=run,  # type: ignore[arg-type]
                    status=st,
                    line=line if shown else None,
                    original=original if shown else None,
                    mutated=mutated if shown else None,
                )
            )
        return out

    @staticmethod
    def run(
        functions: Iterable[FunctionRef] = (),
        results: Iterable[Iterable[MutantResult]] = (),
        tests: dict[str, list[str]] | None = None,
        unmeasured: Iterable[UnmeasuredLocation] = (),
        unreached: Iterable[str] = (),
        baseline_failed: bool = False,
        baseline_output: str | None = None,
    ) -> EngineRun:
        return EngineRun(
            baseline_failed=baseline_failed,
            baseline_output=baseline_output,
            functions=list(functions),
            unmeasured=list(unmeasured),
            unreached=list(unreached),
            results=[r for group in results for r in group],
            tests_by_function=tests or {},
            duration_s=1.0,
        )

    @staticmethod
    def finding(
        severity: str = "info",
        kind: str = "impl_test_changed",
        check: str = "tamper",
        audience: str = "human",
        **extra: Any,
    ) -> Finding:
        return Finding(
            check=check,  # type: ignore[arg-type]
            kind=kind,
            severity=Severity(severity),
            audience=Audience(audience),
            message=extra.pop("message", f"{kind} happened."),
            **extra,
        )


@pytest.fixture
def build() -> type[Build]:
    return Build


@pytest.fixture
def ship_rules() -> RulesFile:
    return RulesFile(
        rules=[
            Rule(
                id="SHIP-003",
                statement="Orders of $50 or more ship free",
                seam="shop.shipping.quote",
            )
        ]
    )
