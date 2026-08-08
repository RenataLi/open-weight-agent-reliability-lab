"""Conversion from internal tool metadata to OpenAI function tools."""

from __future__ import annotations

from collections.abc import Sequence

from agent_reliability_lab.providers.models import ChatTool, FunctionDefinition
from agent_reliability_lab.tools.base import ToolData


def to_chat_tools(schemas: Sequence[ToolData]) -> tuple[ChatTool, ...]:
    """Expose only the function name, description, and JSON input schema."""

    tools: list[ChatTool] = []
    for schema in schemas:
        name = schema.get("name")
        description = schema.get("description")
        parameters = schema.get("parameters")
        if not isinstance(name, str) or not isinstance(description, str):
            raise ValueError("tool metadata requires string name and description")
        if not isinstance(parameters, dict):
            raise ValueError(f"tool {name} has no object parameter schema")
        tools.append(
            ChatTool(
                function=FunctionDefinition(
                    name=name,
                    description=description,
                    parameters=parameters,
                )
            )
        )
    return tuple(tools)
