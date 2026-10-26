"""Check hook events and displayed office state on a fake clock with no user data."""
import sys
from unittest import mock

sys.dont_write_bytecode = True
from support import Checks, Sandbox  # noqa: E402


def actor_id(aid):
    from token_tally.events import ActorKey
    return ActorKey("claude", "legacy-unknown", "s1", aid[6:] if aid.startswith("agent-") else None).public_id


def shown(office, aid):
    agent = office.agents.get(actor_id(aid))
    return agent.public() if agent else None


def main():
    check = Checks()
    clock = [1_000_000.0]

    def later(seconds):
        clock[0] += seconds

    with Sandbox() as sandbox, sandbox.activate():
        import server

        with mock.patch.object(server.time, "time", lambda: clock[0]):
            o = server.Office(projects=sandbox.projects)
            ev = lambda **kw: o.hook({"session_id": "s1", **kw})  # noqa: E731

            # --- a millisecond tool stays on show long enough to walk to its station
            ev(hook_event_name="SessionStart", cwd="/w/p")
            ev(hook_event_name="PreToolUse", tool_name="Read", tool_use_id="t1", tool_input={"file_path": "/w/p/a.py"})
            ev(hook_event_name="PostToolUse", tool_name="Read", tool_use_id="t1")
            p = shown(o, "s1")
            check("quick Read still shown at the filing cabinets", p["status"] == "working" and p["task"] == "files", p["status"])
            later(3.9)
            check("still shown after 3.9 s", shown(o, "s1")["status"] == "working")
            later(0.2)
            check("back to the real status after 4 s", shown(o, "s1")["status"] == "thinking")

            # --- a newer tool replaces the held one
            ev(hook_event_name="PreToolUse", tool_name="Read", tool_use_id="t2", tool_input={"file_path": "/w/p/b.py"})
            ev(hook_event_name="PostToolUse", tool_name="Read", tool_use_id="t2")
            ev(hook_event_name="PreToolUse", tool_name="WebSearch", tool_use_id="t3", tool_input={"query": "x"})
            ev(hook_event_name="PostToolUse", tool_name="WebSearch", tool_use_id="t3")
            check("latest quick tool wins", shown(o, "s1")["task"] == "phone")

            # --- waiting for your sign-off is never covered up
            later(10)
            ev(hook_event_name="PreToolUse", tool_name="Bash", tool_use_id="t4", tool_input={"command": "rm -rf build"})
            ev(hook_event_name="PermissionRequest", tool_name="Bash", tool_input={"command": "rm -rf build"})
            check("sign-off shows straight away", shown(o, "s1")["status"] == "waiting")
            ev(hook_event_name="PostToolUse", tool_name="Bash", tool_use_id="t4")
            ev(hook_event_name="Stop")
            check("finished turn still shows the last tool briefly", shown(o, "s1")["status"] == "working")
            later(5)
            check("then the coffee break", shown(o, "s1")["status"] == "idle")

            # --- temps go home when quiet, even without a SubagentStop
            later(1)
            for aid in ("quiet", "busy", "asking"):
                o.hook({"session_id": "s1", "agent_id": aid, "hook_event_name": "SubagentStart", "agent_type": "Explore"})
            sub = lambda aid, **kw: o.hook({"session_id": "s1", "agent_id": aid, **kw})  # noqa: E731
            sub("quiet", hook_event_name="PreToolUse", tool_name="Grep", tool_use_id="q1", tool_input={"pattern": "x"})
            sub("quiet", hook_event_name="PostToolUse", tool_name="Grep", tool_use_id="q1")
            sub("busy", hook_event_name="PreToolUse", tool_name="Bash", tool_use_id="b1", tool_input={"command": "make test"})
            sub("asking", hook_event_name="PreToolUse", tool_name="Bash", tool_use_id="a1", tool_input={"command": "deploy"})
            sub("asking", hook_event_name="PermissionRequest", tool_name="Bash", tool_input={"command": "deploy"})
            o.tick()
            check("three temps on shift", {actor_id(x) for x in ("agent-quiet", "agent-busy", "agent-asking")} <= set(o.agents))
            later(60)
            o.tick()
            check("quiet temp still there after 60 s", actor_id("agent-quiet") in o.agents)
            later(31)
            o.tick()
            check("quiet temp goes home after 90 s", actor_id("agent-quiet") not in o.agents)
            check("temp with a running tool stays", actor_id("agent-busy") in o.agents)
            check("temp waiting for your sign-off stays", actor_id("agent-asking") in o.agents)
            check("the main worker stays", actor_id("s1") in o.agents)

    return check.finish()


if __name__ == "__main__":
    sys.exit(main())
