"""Answer a question by searching it first, then letting the model call tools until it can answer."""

import contextvars
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

from server import db
from server.shared import llm
from server.agent.answer import NO_ANSWER, Answer, answer_result
from server.agent.prompt import instructions, tool_notes
from server.agent.reading import fact_line, fact_lines, fact_record, spoken_time
from server.agent.tools import SEARCH_RESULTS, TOOLS, Asking, read_arguments, run_tool, step_text
from server.agent.citations import partial_string, shown_so_far
from server.shared.config import AGENT_MODEL
from server.agent.search import search_within
from server.agent.when import question_window

MAX_TOOL_ROUNDS = 5

STEP_PHOTOS = 4

FIRST_SEARCH = "A search for the question found these facts. Answer from them if they are enough; otherwise use the tools."


@dataclass
class Run:
    """Everything one answer gathers on the way: facts shown, tool calls, stored messages, cost."""

    asking: Asking
    question: str
    model: str
    started: float = field(default_factory=time.monotonic)
    seen: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)
    messages: list = field(default_factory=list)
    usd: float = 0.0
    model_calls: int = 0

    def show(self, facts: list[dict]) -> None:
        self.seen.update({fact["id"]: fact for fact in facts})

    def keep_tool_call(self, call_id: str, name: str, arguments: dict, output: str, facts: list, call_ids: list):
        self.calls.append((name, arguments))
        self.messages.append(
            {
                "role": "tool",
                "at": datetime.now().astimezone(),
                "calls": call_ids,
                "content": {
                    "call_id": call_id,
                    "name": name,
                    "arguments": arguments,
                    "output": output,
                    "facts": [fact_record(fact) for fact in facts],
                },
            }
        )


def step_event(kind: str, text: str, facts: list[dict] = ()) -> tuple[str, dict]:
    """Return a step event with a few of the photos its facts came from."""
    images = list(dict.fromkeys(fact["image_path"] for fact in facts if fact["image_path"]))[:STEP_PHOTOS]

    return "step", {"kind": kind, "text": text, "image_paths": images}


def embed_question(question: str) -> str:
    """Embed a question on a connection of its own, so it can run beside other work."""
    with db.pool().connection() as conn:
        return llm.embed(conn, [question], "embed-question")[0]


def first_search(run: Run, history: list):
    """Work out the question's time window while embedding it, then search it. Yield steps; return what to ask."""
    question, asking = run.question, run.asking
    search_step = step_text("search", {"query": question})

    with ThreadPoolExecutor(1) as background:
        vector = background.submit(contextvars.copy_context().run, embed_question, question)
        asking.window, usd = question_window(asking.conn, question, history)
        run.usd += usd
        run.model_calls += 1

        if asking.window[0]:
            start, end = asking.window
            yield step_event("when", f"Looking from {spoken_time(start)} to {spoken_time(end)}")

        yield step_event("search", search_step)
        found = search_within(asking.conn, question, SEARCH_RESULTS, asking.window, asking.moments, vector.result())

    run.show(found)
    run.keep_tool_call("first-search", "search", {"query": question}, fact_lines(found), found, [])
    yield step_event("search", search_step, found)

    return f"{question}\n\n{FIRST_SEARCH}\n{fact_lines(found)}"


def model_input(history: list[tuple[str, str]], question: str) -> list[dict]:
    """Return earlier questions and answers, then the new question, as model input."""
    messages = []

    for asked, answered in history:
        messages += [{"role": "user", "content": asked}, {"role": "assistant", "content": answered}]

    return messages + [{"role": "user", "content": question}]


def model_round(run: Run, request: dict):
    """Ask the model once, yielding the answer text as it streams. Return the model's response."""
    streamed_json, sent_text = "", ""

    with llm.client().responses.stream(**request, text_format=Answer, reasoning={"effort": "low"}) as stream:
        for event in stream:
            if event.type != "response.output_text.delta":
                continue

            if not streamed_json:
                yield step_event("answer", "Writing the answer")

            streamed_json += event.delta
            text = shown_so_far(partial_string(streamed_json, "answer"), run.seen)

            if len(text) > len(sent_text) and text.startswith(sent_text):
                yield "delta", {"text": text[len(sent_text) :]}
                sent_text = text

        response = stream.get_final_response()

    with llm.collect_call_ids() as call_ids:
        run.usd += llm.log_cost(run.asking.conn, "agent", run.model, response.usage)

    run.model_calls += 1
    output = response.model_dump(mode="json")["output"]
    run.messages.append(
        {"role": "assistant", "at": datetime.now().astimezone(), "content": {"output": output}, "calls": call_ids}
    )

    return response


def run_calls(asking: Asking, calls: list[tuple[str, dict | None]]) -> list[tuple]:
    """Run one round's tool calls side by side, each on its own connection; return (output, facts, call ids) in order."""

    def one(name: str, arguments: dict | None, conn) -> tuple:
        with llm.collect_call_ids() as call_ids:
            output, facts = run_tool(Asking(conn, asking.moments, asking.window), name, arguments)

        return output, facts, call_ids

    if len(calls) == 1:
        return [one(*calls[0], asking.conn)]

    def pooled(call: tuple) -> tuple:
        with db.pool().connection() as conn:
            return one(*call, conn)

    with ThreadPoolExecutor(len(calls)) as threads:
        return list(threads.map(lambda call: contextvars.copy_context().run(pooled, call), calls))


def tool_round(run: Run, function_calls: list):
    """Run the tools the model asked for, yielding a step for each. Return their outputs as the next model input."""
    calls = [(call.name, read_arguments(call.arguments)) for call in function_calls]

    for name, arguments in calls:
        yield step_event(name, step_text(name, arguments))

    next_input = []

    for call, (name, arguments), (output, facts, call_ids) in zip(function_calls, calls, run_calls(run.asking, calls)):
        run.show(facts)
        run.keep_tool_call(call.call_id, name, arguments or {}, output, facts, call_ids)
        yield step_event(name, step_text(name, arguments), facts)
        next_input.append({"type": "function_call_output", "call_id": call.call_id, "output": output})

    return next_input


def answer_events(
    conn,
    question: str,
    model: str = AGENT_MODEL,
    history: list[tuple[str, str]] = (),
    use_nearby: bool = True,
    frame: dict | None = None,
    moments: list[str] | None = None,
):
    """Answer a question, yielding each step and piece of the answer as it comes, then the result."""
    llm.require_price(model)
    run = Run(Asking(conn, moments), question, model)
    tools = [tool for tool in TOOLS if use_nearby or tool.name != "nearby"]

    if frame:
        run.show([frame])
        asked = f"{question}\n\nThe question is about this frame:\n{fact_line(frame)}"
    else:
        asked = yield from first_search(run, list(history))

    request = {
        "model": model,
        "instructions": instructions(conn, tool_notes(tools), moments),
        "input": model_input(list(history), asked),
        "tools": [tool.schema() for tool in tools],
        "previous_response_id": None,
    }

    for round_number in range(MAX_TOOL_ROUNDS + 1):
        request["tool_choice"] = "auto" if round_number < MAX_TOOL_ROUNDS else "none"
        response = yield from model_round(run, request)
        function_calls = [item for item in response.output if item.type == "function_call"]

        if not function_calls:
            break

        request["input"] = yield from tool_round(run, function_calls)
        request["previous_response_id"] = response.id

    parsed = response.output_parsed or NO_ANSWER
    run.messages[-1]["content"]["answer"] = parsed.model_dump()
    result = answer_result(parsed, run.seen, run.calls, run.usd, run.model_calls, run.started)

    yield "result", {**result, "messages": run.messages}


def ask_agent(
    conn,
    question: str,
    model: str = AGENT_MODEL,
    history: list[tuple[str, str]] = (),
    use_nearby: bool = True,
    frame: dict | None = None,
    moments: list[str] | None = None,
) -> dict:
    """Answer a question with the tools and return the result."""
    events = answer_events(conn, question, model, history, use_nearby, frame, moments)

    return next(payload for kind, payload in events if kind == "result")
