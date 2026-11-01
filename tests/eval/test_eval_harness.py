"""The evaluation harness itself: privacy checker unit tests + a deterministic full run."""

from __future__ import annotations

import pytest

from scripts.eval_rag import SetupError, _section_matches, privacy_violations, run_eval


def test_privacy_checker_flags_raw_values_and_ignores_tokens_and_public_contacts() -> None:
    clean = [{"instructions": "Loja (67) 3321-4500", "input": "pedido [[ORDER_1]] [[EMAIL_1]]"}]
    assert privacy_violations(clean, ["ana@example.test"], ["(67) 3321-4500"]) == []

    leaked = [{"instructions": None, "input": [{"role": "user", "content": "ana@example.test"}]}]
    found = privacy_violations(leaked, ["ana@example.test"], [])
    assert "payload0:restricted_value" in found and "payload0:email" in found

    tool_output = [{"input": [{"output": '{"tracking_code": "BRAB1234567BR"}'}]}]
    assert "payload0:tracking" in privacy_violations(tool_output, [], [])
    assert "payload0:order_id" in privacy_violations([{"input": "status do pedido 42"}], [], [])


def test_section_prefix_matching() -> None:
    assert _section_matches("3.1 Regras de Parcelamento", "3")
    assert _section_matches("§4.2 Trocas por Defeito", "4.2")
    assert not _section_matches("13. Outros", "1")
    assert not _section_matches(None, "1")


@pytest.mark.integration
async def test_deterministic_eval_passes_hard_gates() -> None:
    try:
        report = await run_eval(live=False)
    except SetupError as exc:
        pytest.skip(str(exc))
    agg = report["aggregate"]
    assert report["mode"].startswith("deterministic")
    assert agg["errored_cases"] == []
    assert agg["tenant_leakage"]["total"] == 0
    assert agg["privacy"]["violations"] == 0
    assert report["passed"] is True
    cases = {case["id"]: case for case in report["cases"]}
    # The privacy gate is only meaningful if model payloads were really captured.
    assert cases["tool-order-status"]["model_payloads"] > 0
    assert sum(case["model_payloads"] for case in report["cases"]) >= len(cases) - 2
