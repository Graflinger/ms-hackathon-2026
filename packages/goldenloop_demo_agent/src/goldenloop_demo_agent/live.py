"""Optional Azure OpenAI sandbox adapter, verified against Agent Framework 1.17.

Only observable text/function contents are retained, not reasoning or raw responses.
"""
import asyncio
import json
import os
from time import perf_counter

from goldenloop_eval import AgentConnection, Case, Observation, ToolCall, sanitize

from .agent import lookup_customer as sandbox_lookup


async def run_live(
    case: Case, revision: str, *, connection: AgentConnection | None = None,
    api_key: str | None = None, instructions: str = "",
) -> Observation:
    if revision not in {"buggy", "fixed"} or case.fixture_version != "synthetic-v1":
        raise ValueError("Unsupported synthetic variant or fixture")
    if connection is None:
        required = ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_CHAT_COMPLETION_MODEL", "AZURE_OPENAI_API_VERSION")
        if any(not os.environ.get(key) for key in required):
            raise ValueError("Live mode requires " + ", ".join(required))
        api_key = api_key if api_key is not None else os.environ.get("AZURE_OPENAI_API_KEY")
        connection = AgentConnection(
            endpoint=os.environ[required[0]], deployment=os.environ[required[1]],
            api_version=os.environ[required[2]], auth="api_key" if api_key else "azure_cli", binding="legacy",
        )
    connection = AgentConnection.model_validate(connection.model_dump(mode="json"))
    if connection.auth == "api_key" and (not api_key or not api_key.strip()):
        raise ValueError("Explicit API key connection requires a resolved credential")
    if connection.auth == "azure_cli" and api_key is not None:
        raise ValueError("Azure CLI connection must not receive an API key")
    try:
        from agent_framework import Agent
        from agent_framework.openai import OpenAIChatCompletionClient
        from azure.identity import AzureCliCredential, get_bearer_token_provider
        from goldenloop_eval.azure_clients import ExplicitAsyncAzureOpenAI
        from httpx import AsyncClient
    except ImportError:
        raise RuntimeError("Install goldenloop-demo-agent[live] for Microsoft Agent Framework live mode") from None

    key = api_key
    secrets = (key,) if key else ()
    if sanitize(connection.model_dump(mode="json"), secrets=secrets) != connection.model_dump(mode="json"):
        raise ValueError("Unsafe agent connection configuration")
    try:
        case = Case.model_validate(sanitize(case.model_dump(mode="json"), secrets=secrets))
    except ValueError:
        raise ValueError("Unsafe execution data") from None
    credential = None if key else AzureCliCredential()
    observation = Observation(case_id=case.id, case_revision=case.revision,
                              agent_revision=revision, mode="live", trace_complete=False)
    started = perf_counter()

    def lookup_customer(customer_id: str) -> dict:
        """Read a customer from the synthetic-only sandbox by exact customer ID."""
        return sandbox_lookup(customer_id)

    agent_instructions = (
        "You are a synthetic customer support demo. Use lookup_customer for customer lookups. "
        "Never claim the data is real. Only use the provided read-only sandbox tool. "
        "Use the exact requested customer ID; retain actual conversation history."
    )
    if revision == "buggy":
        agent_instructions += " Intentional demo bug: always use customer_id C-999 when looking up a customer."
    if instructions:
        agent_instructions += "\n" + sanitize(instructions, secrets=secrets)
    transport = None
    http_client = None
    try:
        http_client = AsyncClient(follow_redirects=False, timeout=60)
        transport = ExplicitAsyncAzureOpenAI(
            azure_endpoint=connection.endpoint, api_version=connection.api_version, timeout=60, max_retries=0,
            http_client=http_client,
            **({"api_key": key} if key else {"azure_ad_token_provider": get_bearer_token_provider(
                credential, "https://cognitiveservices.azure.com/.default"
            )}),
        )
        client = OpenAIChatCompletionClient(
            model=connection.deployment, async_client=transport,
        )
        agent = Agent(client=client, name="GoldenLoopSyntheticCustomer", instructions=agent_instructions,
                      tools=[lookup_customer])
        session = agent.create_session()
        async with asyncio.timeout(120):
            for index, turn in enumerate(case.turns):
                user = sanitize(turn.user, secrets=secrets)
                observation.messages.append({"role": "user", "content": user, "turn": index})
                # Context is untrusted scenario data, not additional agent instructions.
                prompt = user
                if index == 0 and case.context:
                    prompt = "Synthetic scenario data:\n" + sanitize(case.context, secrets=secrets) + "\nUser: " + user
                response = await agent.run(prompt, session=session)
                calls = {}
                completed = set()
                for message in response.messages:
                    for content in message.contents:
                        if content.type == "function_call":
                            arguments = content.arguments
                            if isinstance(arguments, str):
                                arguments = json.loads(arguments)
                            if not content.call_id or content.call_id in calls:
                                raise ValueError("Missing or duplicate function call ID")
                            call = ToolCall(id=content.call_id, tool=content.name,
                                            arguments=sanitize(arguments or {}, secrets=secrets), turn=index,
                                            error="Function invocation error" if content.exception else None)
                            calls[call.id] = call
                            observation.tool_calls.append(call)
                        elif content.type == "function_result":
                            if content.call_id not in calls or content.call_id in completed:
                                raise ValueError("Unmatched function result")
                            call = calls[content.call_id]
                            call.result = sanitize(content.result, secrets=secrets)
                            if content.exception:
                                call.error = "Sandbox tool execution failed"
                            completed.add(content.call_id)
                        elif content.type == "error":
                            raise RuntimeError("Agent Framework returned an error")
                if set(calls) != completed:
                    raise ValueError("Incomplete function trace")
                answer = sanitize(response.text, secrets=secrets)
                if not answer:
                    raise ValueError("Missing assistant answer")
                observation.messages.append({"role": "assistant", "content": answer, "turn": index})
                for name, value in (response.usage_details or {}).items():
                    if type(value) is int:
                        observation.usage[name] = observation.usage.get(name, 0) + value
        observation.trace_complete = True
    except Exception as exc:
        observation.error = f"Live agent execution failed ({type(exc).__name__}); verify deployment/configuration"
    finally:
        try:
            if transport is not None:
                await transport.close()
        finally:
            try:
                if http_client is not None:
                    await http_client.aclose()
            finally:
                if credential is not None:
                    credential.close()
        observation.latency_ms = (perf_counter() - started) * 1000
    return Observation.model_validate(sanitize(observation.model_dump(mode="json"), secrets=secrets))
