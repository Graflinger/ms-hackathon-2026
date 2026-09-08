import asyncio
import json
import os
import time
from contextlib import suppress

from goldenloop_demo_agent import run_case
from goldenloop_eval import Case, Observation, Turn, evaluate, release_hash
from sqlalchemy import select

from .config import clean
from .db import ChatSession, MessageCommand, ReleaseCase, Run, add_event
from .judging import evaluate_with_judge, judge_lineage

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


def bounded_observation(value, case, revision, mode):
    raw = clean(Observation.model_validate(value).model_dump(mode="json"))
    if len(json.dumps(raw)) > 1024 * 1024:
        raise ValueError("Observation size limit exceeded")
    observation = Observation.model_validate(raw)
    if (observation.case_id, observation.case_revision, observation.agent_revision, observation.mode) != (
        case.id,
        case.revision,
        revision,
        mode,
    ):
        raise ValueError("Observation lineage mismatch")
    return observation


class Runner:
    def __init__(self, db, settings):
        self.db = db
        self.settings = settings
        self.tasks = {}
        self.stopping = False
        self.wake = asyncio.Event()

    async def reconcile(self):
        async with self.db.write() as session:
            for run in (await session.scalars(select(Run).where(Run.status == "running"))).all():
                run.status, run.gate, run.error = (
                    "interrupted",
                    "error",
                    "Application stopped during execution",
                )
                add_event(session, "run:" + run.id, "status", {"status": run.status, "gate": run.gate})
            for command in (
                await session.scalars(select(MessageCommand).where(MessageCommand.status == "running"))
            ).all():
                command.status = "interrupted"
                chat = await session.get(ChatSession, command.session_id)
                chat.status, chat.trace_complete, chat.error = (
                    "interrupted",
                    False,
                    "Application stopped during execution",
                )
                add_event(session, "chat:" + chat.id, "status", {"status": "interrupted", "id": command.id})

    async def loop(self):
        try:
            while not self.stopping:
                for key, task in list(self.tasks.items()):
                    if task.done():
                        # Retrieve errors; persistence failures are reconciled at restart, never passed.
                        with suppress(Exception, asyncio.CancelledError):
                            task.result()
                        del self.tasks[key]
                for model, kind in ((Run, "run"), (MessageCommand, "chat")):
                    if any(key.startswith(kind + ":") for key in self.tasks):
                        continue
                    async with self.db.write() as session:
                        item = await session.scalar(
                            select(model).where(model.status == "queued").order_by(model.created_at).limit(1)
                        )
                        if item is None:
                            continue
                        item.status = "running"
                        item_id = item.id
                        if kind == "chat":
                            chat = await session.get(ChatSession, item.session_id)
                            chat.status = "running"
                            stream = "chat:" + chat.id
                        else:
                            stream = "run:" + item_id
                        add_event(session, stream, "status", {"status": "running", "id": item_id})
                    self.tasks[kind + ":" + item_id] = asyncio.create_task(
                        self.execute_run(item_id) if kind == "run" else self.execute_chat(item_id)
                    )
                with suppress(TimeoutError):
                    await asyncio.wait_for(self.wake.wait(), self.settings.poll_interval)
        finally:
            self.stopping = True
            tasks = list(self.tasks.values())
            for task in tasks:
                if not task.cancelling():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def execute_run(self, run_id):
        try:
            async with self.db.sessions() as session:
                run = await session.get(Run, run_id)
                if run.status != "running":
                    return
                revision, mode = run.agent_revision, run.mode
                judging = run.lineage["judge"]
                cases = [
                    Case.model_validate(row.payload)
                    for row in (
                        await session.scalars(
                            select(ReleaseCase)
                            .where(ReleaseCase.release_id == run.release_id)
                            .order_by(ReleaseCase.case_id)
                        )
                    ).all()
                ]
                if release_hash(cases) != run.lineage["content_hash"]:
                    raise ValueError("Release hash mismatch")
                if any(clean(case.model_dump(mode="json")) != case.model_dump(mode="json") for case in cases):
                    raise ValueError("Redaction policy changed; review a new revision before execution")
                if judge_lineage(self.settings, cases, judging.get("selection", "none")) != judging:
                    raise ValueError("Judge configuration changed after submission")
                if mode == "live" and (
                    not self.settings.allow_live
                    or any(
                        run.lineage[key] != os.getenv(env)
                        for key, env in (
                            ("agent_model", "AZURE_OPENAI_CHAT_COMPLETION_MODEL"),
                            ("agent_endpoint", "AZURE_OPENAI_ENDPOINT"),
                            ("agent_api_version", "AZURE_OPENAI_API_VERSION"),
                        )
                    )
                ):
                    raise ValueError("Live configuration changed after submission")
            async with asyncio.timeout(self.settings.run_timeout):
                deadline = time.monotonic() + self.settings.run_timeout
                execution_error = False
                for case in cases:
                    try:
                        async with asyncio.timeout(self.settings.case_timeout):
                            observation = bounded_observation(
                                await run_case(case, revision=revision, mode=mode), case, revision, mode
                            )
                    except Exception:  # noqa: BLE001 - untrusted adapter failures must become safe error observations
                        observation = Observation(
                            case_id=case.id,
                            case_revision=case.revision,
                            agent_revision=revision,
                            mode=mode,
                            trace_complete=False,
                            error="Agent execution failed or exceeded its deadline",
                        )
                    execution_error |= observation.error is not None
                    if judging["configured"] and any(check.kind == "judge" for check in case.checks):
                        result = await evaluate_with_judge(case, observation, judging, deadline)
                    else:
                        result = await asyncio.to_thread(evaluate, case, observation)
                    payload = clean(
                        {**result.model_dump(mode="json"), "observation": observation.model_dump(mode="json")}
                    )
                    if len(json.dumps(payload)) > 2 * 1024 * 1024:
                        raise ValueError("Evaluation result size limit exceeded")
                    async with self.db.write() as session:
                        run = await session.get(Run, run_id)
                        if run.status != "running":
                            return
                        run.results = [*run.results, payload]
                        add_event(session, "run:" + run_id, "result", payload)
                async with self.db.write() as session:
                    run = await session.get(Run, run_id)
                    if run.status != "running":
                        return
                    gates = [result["gate"] for result in run.results]
                    run.gate = (
                        "error" if not gates or "error" in gates else "fail" if "fail" in gates else "pass"
                    )
                    run.status = "failed" if execution_error else "completed"
                    run.error = "One or more agent executions failed" if execution_error else None
                    add_event(session, "run:" + run_id, "status", {"status": run.status, "gate": run.gate})
        except asyncio.CancelledError:
            await self.fail_run(
                run_id,
                "interrupted" if self.stopping else "cancelled",
                "Application stopped during execution" if self.stopping else "Cancellation requested",
            )
            raise
        except Exception:  # noqa: BLE001 - task boundary must persist a fail-closed terminal state
            await self.fail_run(run_id, "failed", "Evaluation failed or exceeded its deadline")

    async def fail_run(self, run_id, status, error):
        async with self.db.write() as session:
            run = await session.get(Run, run_id)
            if run.status in TERMINAL:
                return
            run.status, run.gate, run.error = status, "error", error
            add_event(session, "run:" + run_id, "status", {"status": status, "gate": "error"})

    async def execute_chat(self, command_id):
        try:
            async with self.db.sessions() as session:
                command = await session.get(MessageCommand, command_id)
                if command.status != "running":
                    return
                chat = await session.get(ChatSession, command.session_id)
                turns = [Turn(user=m["content"]) for m in chat.messages if m["role"] == "user"]
                turns.append(Turn(user=command.content))
                case = Case(id=chat.id, title=chat.title, turns=turns, source={"synthetic": True})
                revision, turn, chat_id = chat.agent_revision, command.turn, chat.id
                prior = next(
                    (m["content"] for m in reversed(chat.messages) if m["role"] == "assistant"), None
                )
                current = case.model_copy(update={"turns": [turns[-1]]})
            async with asyncio.timeout(self.settings.case_timeout):
                observation = bounded_observation(
                    await run_case(current, revision=revision, mode="mock"), current, revision, "mock"
                )
            if observation.error or not observation.trace_complete:
                raise ValueError("Chat execution failed")
            messages = observation.messages
            if (
                [m.get("role") for m in messages] != ["user", "assistant"]
                or any(m.get("turn") != 0 for m in messages)
                or any(call.turn != 0 for call in observation.tool_calls)
            ):
                raise ValueError("Incomplete chat turn")
            # This mock agent's no-tool follow-up echoes its last generated answer. Continue from
            # the persisted answer, never reexecute earlier lookups or regenerate their outputs.
            # A live/stateful agent must supply a real history-aware adapter instead of this rule.
            if prior is not None and not observation.tool_calls:
                messages[1]["content"] = "From my previous answer: " + prior
            for message in messages:
                message["turn"] = turn
            call_ids = {
                call.id: f"{command_id}:call:{index}" for index, call in enumerate(observation.tool_calls)
            }
            if len(call_ids) != len(observation.tool_calls):
                raise ValueError("Duplicate tool call IDs")
            calls = [
                {
                    **call.model_dump(mode="json"),
                    "id": call_ids[call.id],
                    "turn": turn,
                    "parent_id": call_ids.get(call.parent_id, call.parent_id),
                }
                for call in observation.tool_calls
            ]
            if len(json.dumps(clean([messages, calls]))) > 1024 * 1024:
                raise ValueError("Chat turn size limit exceeded")
            async with self.db.write() as session:
                command = await session.get(MessageCommand, command_id)
                chat = await session.get(ChatSession, chat_id)
                command.status, chat.status, chat.error = "completed", "completed", None
                for index, message in enumerate(messages):
                    message["id"] = f"{command_id}:{index}"
                    message["command_id"] = command_id
                chat.messages = clean([*chat.messages, *messages])
                chat.tool_calls = clean([*chat.tool_calls, *calls])
                chat.trace_complete = observation.trace_complete
                for message in messages:
                    add_event(session, "chat:" + chat_id, "message", message)
                for call in calls:
                    add_event(session, "chat:" + chat_id, "tool_call", call)
                add_event(
                    session,
                    "chat:" + chat_id,
                    "status",
                    {"status": "completed", "id": command_id, "trace_complete": chat.trace_complete},
                )
        except asyncio.CancelledError:
            await self.fail_chat(command_id, "interrupted")
            raise
        except Exception:  # noqa: BLE001 - never expose provider exception text to chat clients
            await self.fail_chat(command_id, "failed")

    async def fail_chat(self, command_id, status):
        async with self.db.write() as session:
            command = await session.get(MessageCommand, command_id)
            if command.status in TERMINAL:
                return
            command.status = status
            chat = await session.get(ChatSession, command.session_id)
            chat.status, chat.trace_complete = status, False
            chat.error = (
                "Chat execution interrupted"
                if status == "interrupted"
                else "Chat execution failed or timed out"
            )
            add_event(session, "chat:" + chat.id, "status", {"status": status, "id": command_id})
