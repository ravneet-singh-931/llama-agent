"""
Regression tests for the chat fixes in CHANGELOG 3.3.3:

- SSE escaping keeps literal backslashes (code like print("a\\nb")) intact.
- Guest conversations are shared across API replicas through Redis.
- Critic / Reflect really run on specialist answers, BEFORE streaming.
- Empty model answers never reach the user as a blank bubble.
- Team mode honours Parallel (concurrent levels) vs sequential.

All offline: no Groq calls, no real Redis.
Run with: pytest tests/test_chat_fixes.py -v
"""

import fnmatch
import os
import sys
import threading
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("GROQ_API_KEY", "test-key")


# ============ SSE ESCAPING ============

def _unescape_like_frontend(data: str) -> str:
    """Python mirror of unescapeSse() in frontend/src/lib/api.ts."""
    import re
    return re.sub(r"\\([\\nr])",
                  lambda m: {"n": "\n", "r": "\r", "\\": "\\"}[m.group(1)], data)


def test_escape_sse_round_trips_backslashes_and_newlines():
    from api import escape_sse
    cases = ['print("a\\nb")\n', "C:\\new\\road", "a\r\nb", "\\\\n", "end\\"]
    for text in cases:
        escaped = escape_sse(text)
        assert "\n" not in escaped and "\r" not in escaped  # SSE framing safe
        assert _unescape_like_frontend(escaped) == text


# ============ SHARED (REDIS) CONVERSATION MEMORY ============

class _FakeRedis:
    """Just enough of redis-py for ConversationMemory (shared dict store)."""

    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.ttls = {}

    def ping(self):
        return True

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v, ex=None):
        self.store[k] = v
        if ex:
            self.ttls[k] = ex

    def delete(self, k):
        self.store.pop(k, None)

    def sadd(self, k, *vals):
        self.store.setdefault(k, set()).update(vals)

    def srem(self, k, *vals):
        self.store.get(k, set()).difference_update(vals)

    def smembers(self, k):
        return set(self.store.get(k, set()))

    def expire(self, k, ttl):
        self.ttls[k] = ttl

    def mget(self, keys):
        return [self.store.get(k) for k in keys]

    def scan_iter(self, match="*", count=None):
        return [k for k in list(self.store) if fnmatch.fnmatch(k, match)
                and isinstance(self.store[k], str)]

    def pipeline(self):
        outer = self

        class _P:
            def __init__(self):
                self.ops = []

            def __getattr__(self, name):
                return lambda *a, **kw: self.ops.append((name, a, kw))

            def execute(self):
                for name, a, kw in self.ops:
                    getattr(outer, name)(*a, **kw)

        return _P()


def _redis_memory(tmp_path, shared):
    from modules.memory import ConversationMemory
    mem = ConversationMemory(memory_file=str(tmp_path / "unused.json"),
                             use_redis=False)
    mem.redis = _FakeRedis(shared)  # two instances, one Redis = two pods
    return mem


def test_guest_history_is_shared_between_replicas(tmp_path):
    shared = {}
    pod_a = _redis_memory(tmp_path, shared)
    pod_b = _redis_memory(tmp_path, shared)

    cid = pod_a.add_message("user", "hello from pod A", user_id="guest-1",
                            conversation_id="c-1")
    pod_a.add_message("assistant", "hi!", user_id="guest-1", conversation_id=cid)

    # The other pod sees the same conversation, title and messages.
    convs = pod_b.get_conversations("guest-1")
    assert [c["id"] for c in convs] == [cid]
    assert convs[0]["title"] == "hello from pod A"
    assert [m["content"] for m in pod_b.get_messages("guest-1", cid)] == [
        "hello from pod A", "hi!"]
    assert "guest-1" not in str(pod_b.get_conversations("guest-2"))
    # Nothing was written to the pod-local JSON file.
    assert not (tmp_path / "unused.json").exists()
    # Keys carry a TTL so abandoned guest chats expire.
    assert pod_a.redis.ttls["llama:conv:" + cid] == pod_a.ttl

    # Deleting on pod B removes it for pod A too; other users can't delete.
    assert pod_b.delete_conversation("guest-2", cid) is False
    assert pod_b.delete_conversation("guest-1", cid) is True
    assert pod_a.get_conversations("guest-1") == []


def test_redis_memory_never_rebinds_another_users_conversation(tmp_path):
    mem = _redis_memory(tmp_path, {})
    mem.add_message("user", "mine", user_id="alice", conversation_id="same")
    new_id = mem.add_message("user", "intruder", user_id="bob",
                             conversation_id="same")
    assert new_id != "same"
    assert [m["content"] for m in mem.get_messages("alice", "same")] == ["mine"]
    assert [m["content"] for m in mem.get_messages("bob", new_id)] == ["intruder"]


def test_memory_falls_back_to_json_when_redis_breaks(tmp_path):
    mem = _redis_memory(tmp_path, {})

    def boom(*a, **kw):
        raise ConnectionError("redis down")

    mem.redis.get = boom
    cid = mem.add_message("user", "still saved", user_id="g", conversation_id="x")
    assert mem.redis is None  # switched to the JSON file
    assert mem.get_messages("g", cid)[0]["content"] == "still saved"
    assert (tmp_path / "unused.json").exists()


# ============ CRITIC / REFLECT BEFORE STREAMING ============

def _tokens(out):
    return "".join(o["content"] for o in out if o["type"] == "token").strip()


def test_specialist_reflection_revises_answer_before_it_streams(monkeypatch):
    from multi_agent import ResearchAgent
    agent = ResearchAgent()
    monkeypatch.setattr("modules.agent_tools.run_tool_rounds",
                        lambda c, m, msgs, tools, **kw: (msgs, ["⚙️ web_search"], "draft answer"))
    monkeypatch.setattr(agent, "critique", lambda task, ans, sources=None: "Missing the date." if ans == "draft answer" else "")
    monkeypatch.setattr(agent, "_revise", lambda task, ans, c, sources=None: "revised answer with the date")

    out = list(agent._stream_with_tools("t", critique=True, reflect=True))
    stages = [o.get("stage") for o in out if o["type"] == "reasoning"]
    assert stages == ["critic", "reflection"]
    # Only the revised answer streams - the draft never reaches the user.
    assert _tokens(out) == "revised answer with the date"
    # Reasoning events come before the first answer token.
    first_token = next(i for i, o in enumerate(out) if o["type"] == "token")
    last_reason = max(i for i, o in enumerate(out) if o["type"] == "reasoning")
    assert last_reason < first_token


def test_critic_only_keeps_answer_and_off_skips_review(monkeypatch):
    from multi_agent import ResearchAgent
    agent = ResearchAgent()
    monkeypatch.setattr("modules.agent_tools.run_tool_rounds",
                        lambda c, m, msgs, tools, **kw: (msgs, [], "answer"))
    calls = []
    monkeypatch.setattr(agent, "critique", lambda t, a, sources=None: calls.append(a) or "Too short.")
    monkeypatch.setattr(agent, "_revise", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no revise")))

    out = list(agent._stream_with_tools("t", critique=True, reflect=False))
    assert _tokens(out) == "answer"
    assert [o["stage"] for o in out if o["type"] == "reasoning"] == ["critic"]

    calls.clear()
    out = list(agent._stream_with_tools("t"))  # both off
    assert calls == [] and _tokens(out) == "answer"
    assert not [o for o in out if o["type"] == "reasoning"]


# ============ EMPTY ANSWERS ============

class _Client:
    """Streaming calls return no content; non-streaming returns `rescue`."""

    def __init__(self, rescue):
        self.calls = []
        outer = self

        class _Completions:
            def create(self, **kw):
                outer.calls.append(kw)
                if kw.get("stream"):
                    return []  # model streamed nothing
                msg = types.SimpleNamespace(content=rescue)
                return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

        self.chat = types.SimpleNamespace(completions=_Completions())


def test_empty_wrap_up_stream_is_rescued_on_fast_tier(monkeypatch):
    from multi_agent import ResearchAgent
    from modules.model_tiers import resolve_model
    agent = ResearchAgent()
    agent.client = _Client(rescue="rescued answer")
    monkeypatch.setattr("modules.agent_tools.run_tool_rounds",
                        lambda c, m, msgs, tools, **kw: (msgs, [], None))
    out = list(agent._stream_with_tools("t"))
    assert _tokens(out) == "rescued answer"
    assert agent.client.calls[-1]["model"] == resolve_model("fast")
    assert not agent.client.calls[-1].get("stream")


def test_empty_answer_everywhere_yields_a_visible_message(monkeypatch):
    from multi_agent import ResearchAgent
    agent = ResearchAgent()
    agent.client = _Client(rescue="")
    monkeypatch.setattr("modules.agent_tools.run_tool_rounds",
                        lambda c, m, msgs, tools, **kw: (msgs, [], ""))
    out = list(agent._stream_with_tools("t"))
    assert _tokens(out) == agent.EMPTY_ANSWER.strip()


# ============ TEAM: PARALLEL VS SEQUENTIAL ============

def _team(monkeypatch, delay=0.3):
    from multi_agent import MultiAgentOrchestrator
    orch = MultiAgentOrchestrator()
    steps = [
        {"id": "s1", "agent": "research", "instruction": "a", "depends_on": []},
        {"id": "s2", "agent": "coder", "instruction": "b", "depends_on": []},
        {"id": "s3", "agent": "writer", "instruction": "c", "depends_on": ["s1", "s2"]},
    ]
    monkeypatch.setattr(orch, "_plan_dag", lambda task: steps)
    active = {"now": 0, "max": 0}
    lock = threading.Lock()

    def fake_stream(name):
        def _gen(prompt, critique=False, reflect=False):
            with lock:
                active["now"] += 1
                active["max"] = max(active["max"], active["now"])
            time.sleep(delay)
            with lock:
                active["now"] -= 1
            yield {"type": "token", "content": f"{name} out"}
        return _gen

    for key, agent in orch.agents.items():
        monkeypatch.setattr(agent, "execute_stream", fake_stream(key))
        monkeypatch.setattr(agent, "critique", lambda t, a, sources=None: "")
    return orch, active


def test_team_parallel_runs_independent_steps_concurrently(monkeypatch):
    orch, active = _team(monkeypatch)
    out = list(orch.collaborate_stream("task", critique=False, reflect=False, parallel=True))
    assert active["max"] == 2  # s1 and s2 overlapped
    assert any(o.get("stage") == "parallel" for o in out)
    done = [o["step"] for o in out if o["type"] == "agent_done"]
    assert sorted(done[:2]) == ["s1", "s2"] and done[2] == "s3"
    # Every agent's start/tokens/done stay grouped together.
    for sid in ("s1", "s2", "s3"):
        i = next(i for i, o in enumerate(out) if o["type"] == "agent_start" and o["step"] == sid)
        assert out[i + 1]["type"] == "token" and out[i + 2]["type"] == "agent_done"
    assert out[-1]["type"] == "final" and "writer out" in out[-1]["content"]


def test_team_sequential_when_parallel_off(monkeypatch):
    orch, active = _team(monkeypatch, delay=0.05)
    out = list(orch.collaborate_stream("task", critique=False, reflect=False, parallel=False))
    assert active["max"] == 1
    assert not any(o.get("stage") == "parallel" for o in out)
    assert [o["step"] for o in out if o["type"] == "agent_done"] == ["s1", "s2", "s3"]


def test_team_critic_note_is_a_reasoning_event(monkeypatch):
    orch, _ = _team(monkeypatch, delay=0)
    monkeypatch.setattr(orch.agents["analyzer"], "critique", lambda t, a, sources=None: "Add sources.")
    out = list(orch.collaborate_stream("task", critique=True, reflect=False, parallel=False))
    assert {"type": "reasoning", "stage": "critic", "content": "Add sources."} in out
    assert "Add sources." in out[-1]["content"]


# ============ TEAM: ONE JOB PER STEP, PARALLEL PLANS, CLOSED FENCES ============

def test_fence_closer_closes_only_unterminated_blocks():
    from multi_agent import _fence_closer
    assert _fence_closer("text\n```py\nx = 1\n```\n") == ""
    assert _fence_closer("no code at all") == ""
    closer = _fence_closer("intro\n```html\n<div>cut off")
    assert closer.startswith("\n```") and "cut off at the length limit" in closer
    assert _fence_closer("intro\n```html\n<div>cut off" + closer) == ""


def test_team_step_closes_fence_left_open_by_token_limit(monkeypatch):
    orch, _ = _team(monkeypatch, delay=0)

    def truncated(prompt, critique=False, reflect=False):
        yield {"type": "token", "content": "Research\n```css\n.banner h3 { font"}

    monkeypatch.setattr(orch.agents["research"], "execute_stream", truncated)
    out = list(orch.collaborate_stream("task", critique=False, reflect=False, parallel=False))
    s1 = next(o for o in out if o["type"] == "agent_done" and o["step"] == "s1")
    assert s1["content"].count("```") == 2  # block closed before the next agent
    # The next agent's section starts outside any code block.
    from multi_agent import _fence_closer
    assert _fence_closer(out[-1]["content"]) == ""


def test_step_prompt_limits_agent_to_its_own_step():
    from multi_agent import MultiAgentOrchestrator
    orch = MultiAgentOrchestrator()
    step = {"id": "s1", "agent": "research", "depends_on": [],
            "instruction": "Research the todo-app market"}
    # An intermediate step sees only its own instruction (context isolation).
    prompt = orch._build_step_prompt(step, {}, "research AND write the landing page code",
                                     is_final=False)
    assert prompt.startswith("YOUR TASK: Research the todo-app market")
    assert "landing page" not in prompt
    assert "agent team" not in prompt          # no team framing -> no meta-talk


def test_planner_is_told_to_maximise_parallelism(monkeypatch):
    from multi_agent import MultiAgentOrchestrator
    orch = MultiAgentOrchestrator()
    seen = {}

    def fake_chat(messages, **kw):
        seen["prompt"] = messages[-1]["content"]
        msg = types.SimpleNamespace(content='[{"id":"s1","agent":"research","depends_on":[],"instruction":"r"},'
                                            '{"id":"s2","agent":"coder","depends_on":[],"instruction":"c"},'
                                            '{"id":"s3","agent":"writer","depends_on":["s1","s2"],"instruction":"w"}]')
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    monkeypatch.setattr(orch.agents["planner"], "_chat", fake_chat)
    steps = orch._plan_dag("research the market AND write the code")
    assert "MAXIMISE PARALLELISM" in seen["prompt"]
    levels, ok = orch._topo_levels(steps)
    assert ok and sorted(levels[0]) == ["s1", "s2"] and levels[1] == ["s3"]


def test_default_dag_runs_planning_and_research_together():
    from multi_agent import MultiAgentOrchestrator
    orch = MultiAgentOrchestrator()
    levels, ok = orch._topo_levels(orch._default_dag("write code for a landing page"))
    assert ok and sorted(levels[0]) == ["s1", "s2"]
