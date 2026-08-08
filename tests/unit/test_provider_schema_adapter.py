from __future__ import annotations

from agent_reliability_lab.providers.schema_adapter import to_chat_tools
from agent_reliability_lab.tools.registry import build_registry


def test_schema_adapter_exposes_only_openai_function_fields() -> None:
    registry = build_registry()
    schemas = [registry["rollback_deployment"].json_schema()]
    tools = to_chat_tools(schemas)

    assert len(tools) == 1
    assert tools[0].type == "function"
    assert tools[0].function.name == "rollback_deployment"
    properties = tools[0].function.parameters["properties"]
    assert isinstance(properties, dict)
    assert "expected_revision" in properties
    payload = tools[0].model_dump(mode="json")
    assert set(payload) == {"type", "function"}
    assert "protected" not in payload["function"]
