"""Thin AgentDojo adapter around the framework-independent approval gate."""
from __future__ import annotations

from typing import Any

from agentdojo.agent_pipeline import (
    AgentPipeline,
    BasePipelineElement,
    InitQuery,
    SystemMessage,
    ToolsExecutionLoop,
    ToolsExecutor,
)
from agentdojo.agent_pipeline.tool_execution import is_string_list, literal_eval
from agentdojo.functions_runtime import EmptyEnv, Env, FunctionsRuntime
from agentdojo.types import ChatToolResultMessage, text_content_block_from_string

from gate_and_experiments.contracts import DecisionContext, GateState, ToolCall
from gate_and_experiments.gate import Gate


class GateStateInitializer(BasePipelineElement):
    """Attach fresh gate state to AgentDojo's per-task extra arguments."""

    name = "approval_gate_state"

    def __init__(self, budget: int, task_id: str | None = None):
        if budget < 0:
            raise ValueError("Escalation budget cannot be negative.")
        self.budget = budget
        self.task_id = task_id
        self.last_state: GateState | None = None
        self.last_messages: list[Any] = []

    def query(self, query, runtime, env=EmptyEnv(), messages=None, extra_args=None):
        """Create new state at the beginning of each task invocation."""
        self.last_state = GateState(self.budget)
        self.last_messages = []
        return query, runtime, env, messages or [], {"approval_gate": self.last_state}


class AgentDojoGatedExecutor(ToolsExecutor):
    """Convert AgentDojo calls to shared contracts and enforce the generic gate."""

    def __init__(self, gate: Gate, **kwargs: Any):
        super().__init__(**kwargs)
        self.gate = gate

    def query(self, query, runtime: FunctionsRuntime, env: Env = EmptyEnv(), messages=None, extra_args=None):
        """Gate each assistant tool call and execute only final approvals."""
        messages = messages or []
        extra_args = extra_args or {}
        if not messages or messages[-1]["role"] != "assistant" or not messages[-1].get("tool_calls"):
            return query, runtime, env, messages, extra_args

        state: GateState = extra_args["approval_gate"]
        history = extra_args.setdefault("approval_gate_messages", [])
        history.extend(message for message in messages if message not in history)
        visible_messages = list(history)
        state_initializer = getattr(self, "gate_state_initializer", None)
        if state_initializer is not None:
            state_initializer.last_messages = list(visible_messages)
        results = []
        for dojo_call in messages[-1]["tool_calls"]:
            # Match AgentDojo's executor behavior for list arguments serialized by an LLM.
            arguments = dict(dojo_call.args)
            for name, value in arguments.items():
                if isinstance(value, str) and is_string_list(value):
                    arguments[name] = literal_eval(value)
            call = ToolCall(
                call_id=str(dojo_call.id),
                function=dojo_call.function,
                args=arguments,
            )
            context = DecisionContext(
                user_task=query,
                messages=tuple(visible_messages),
                environment=env,
                escalation_budget=state.budget,
                escalations_used=state.used,
                metadata={"agentdojo_task_id": state_initializer.task_id} if state_initializer else {},
                executed_calls=tuple(record for record in state.records if record.get("invoked")),
            )
            record = self.gate.decide(call, context, state, task_id=str(query))
            if record["final"] == "APPROVE":
                record["invoked"] = True
                try:
                    output, error = runtime.run_function(env, dojo_call.function, arguments)
                    content = self.output_formatter(output)
                    record["tool_error"] = error
                    if str(error or "").startswith(("ValidationError:", "ToolNotFoundError:")):
                        record["execution_status"] = "not_started"
                except Exception as exc:
                    output, error = "", f"{type(exc).__name__}: {exc}"
                    content = ""
                    record["tool_error"] = error
            else:
                content = ""
                error = f"Blocked by approval policy ({record['by']}). Do not retry this call."
            result = ChatToolResultMessage(
                role="tool",
                content=[text_content_block_from_string(content)],
                tool_call_id=dojo_call.id,
                tool_call=dojo_call,
                error=error,
            )
            results.append(result)
            visible_messages.append(result)
            history.append(result)
            if state_initializer is not None:
                state_initializer.last_messages = list(visible_messages)
        return query, runtime, env, [*messages, *results], extra_args


def build_agentdojo_pipeline(
    llm: BasePipelineElement,
    gate: Gate,
    budget: int,
    system_message: str,
    task_id: str | None = None,
) -> AgentPipeline:
    """Assemble an AgentDojo pipeline with the approval gate in its tool loop.

    Accepting an already-created LLM element keeps provider credentials and
    model selection outside the gate module. The same LLM instance is
    used to propose calls and continue after tool results.
    """
    if budget < 0:
        raise ValueError("Escalation budget cannot be negative.")
    executor = AgentDojoGatedExecutor(gate)
    state_initializer = GateStateInitializer(budget, task_id=task_id)
    executor.gate_state_initializer = state_initializer
    return AgentPipeline([
        SystemMessage(system_message),
        InitQuery(),
        state_initializer,
        llm,
        ToolsExecutionLoop([executor, llm]),
    ])
