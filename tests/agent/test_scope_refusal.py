"""A public refusal is not factual grounding or a general guardrail exemption."""

import pytest

from app.agents.guardrails import (
    OUT_OF_SCOPE,
    OUT_OF_SCOPE_EN,
    answer_uses_tool_facts,
    is_out_of_scope_refusal,
    output_violation,
)
from app.agents.instructions import POLICY_TEXT


@pytest.mark.parametrize("reply", [OUT_OF_SCOPE, OUT_OF_SCOPE_EN])
def test_public_scope_reply_is_allowed_but_ungrounded(reply):
    assert is_out_of_scope_refusal(reply)
    assert not answer_uses_tool_facts(reply, [])
    assert (
        output_violation(
            reply,
            records=[],
            labels=set(),
            instructions=POLICY_TEXT,
            policy_text=POLICY_TEXT,
        )
        is None
    )


@pytest.mark.parametrize(
    "extra", [" O preço é R$ 100.", " Temos 3 unidades em estoque.", " [S1]", " Entrega em 7 dias."]
)
def test_scope_prefix_does_not_exempt_extra_claims(extra):
    answer = OUT_OF_SCOPE + extra
    assert not is_out_of_scope_refusal(answer)
    assert (
        output_violation(
            answer, records=[], labels=set(), instructions=POLICY_TEXT, policy_text=POLICY_TEXT
        )
        is not None
    )
