#!/usr/bin/env python3
"""In-container OpenHands SDK runner for the JMT panel (the OpenHands leg).

Runs the agent headless on <workdir> with the task prompt; the agent reads the mounted
SKILL.md and does its OWN PIT runs + test writing via its bash/file tools. Scoring is done
OUTSIDE this process (by panel.py via pit.py) — never self-reported. Run with the 3.12 venv:
  /opt/ohvenv/bin/python panel_oh_run.py <workdir> <prompt>
Env: OC_BASE, OC_MODEL, OC_KEY (+ optional OH_MAX_ITER). LLM config per the thinking-budget
playbook: agent max_output_tokens=32768 (262k ctx; big test-edit tool calls must not truncate),
temperature=0.0, native tool calls; condenser tools off, 4096.
"""
import os, sys, traceback, time, json

# --- TCP keepalive on ALL sockets (whitebox philosophy: anomaly->investigate->fix, NOT timeout->kill).
# A SLOW inference response still waits (1y request timeout). A DEAD connection (peer/proxy gone) is
# detected by keepalive probes - idle 60s, then every 15s x4 (~120s) -> read error -> num_retries reconnects.
import socket as _socket
_KA = (int(os.environ.get('JMT_TCP_KEEPIDLE', '60')),
       int(os.environ.get('JMT_TCP_KEEPINTVL', '15')),
       int(os.environ.get('JMT_TCP_KEEPCNT', '4')))
_OrigSocket = _socket.socket
class _KeepAliveSocket(_OrigSocket):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        try:
            self.setsockopt(_socket.SOL_SOCKET, _socket.SO_KEEPALIVE, 1)
            self.setsockopt(_socket.IPPROTO_TCP, _socket.TCP_KEEPIDLE, _KA[0])
            self.setsockopt(_socket.IPPROTO_TCP, _socket.TCP_KEEPINTVL, _KA[1])
            self.setsockopt(_socket.IPPROTO_TCP, _socket.TCP_KEEPCNT, _KA[2])
        except (OSError, AttributeError):
            pass
_socket.socket = _KeepAliveSocket

workdir, prompt = sys.argv[1], sys.argv[2]
try:
    from openhands.sdk import LLM, Agent, Conversation, LocalWorkspace
    from openhands.tools.preset.default import (get_default_tools, register_builtins_agents,
        load_agents_from_dir, agent_definition_to_factory, register_agent_if_absent)
    from pathlib import Path as _Path
    from openhands.sdk.context.condenser import LLMSummarizingCondenser
    from pydantic import SecretStr

    base = os.environ["OC_BASE"]
    model = "openai/" + os.environ["OC_MODEL"]
    key = SecretStr(os.environ["OC_KEY"])
    _R = dict(timeout=31_536_000, num_retries=100, retry_min_wait=3, retry_max_wait=60)
    llm = LLM(model=model, base_url=base, api_key=key, usage_id="jmt-oh",
              max_output_tokens=32768, temperature=0.0, native_tool_calling=True, **_R)
    cond = LLM(model=model, base_url=base, api_key=key, usage_id="jmt-cond",
               max_output_tokens=4096, temperature=0.0, native_tool_calling=False, **_R)
    register_builtins_agents(enable_browser=False)  # bash-runner / code-explorer / general-purpose subagents
    # register custom JMT sub-agent types (mutation-tester) from JMT_HOME/docker/subagents;
    # register_agent_if_absent will NOT let the builtins overwrite these.
    _sa_dir = _Path(os.environ.get("JMT_HOME", os.getcwd())) / "docker" / "subagents"
    if _sa_dir.is_dir():
        for _ad in load_agents_from_dir(_sa_dir):
            register_agent_if_absent(_ad.name, agent_definition_to_factory(_ad), _ad)
    agent = Agent(llm=llm, tools=get_default_tools(enable_browser=False, enable_sub_agents=True),
                  condenser=LLMSummarizingCondenser(llm=cond, max_size=40, keep_first=2))
    _EV = os.environ.get("OH_EVENT_LOG")
    def _sink(event):
        # log EVERY dialog event (messages, tool calls + args, observations, sub-agent spawns/results)
        if not _EV:
            return
        try:
            rec = event.model_dump(mode="json")
        except Exception:
            rec = {"repr": str(event)[:4000]}
        rec["_kind"] = type(event).__name__
        try:
            with open(_EV, "a") as _f:
                _f.write(json.dumps(rec, default=str) + "\n")
        except Exception:
            pass
    conv = Conversation(agent=agent, workspace=LocalWorkspace(working_dir=workdir),
                        max_iteration_per_run=int(os.environ.get("OH_MAX_ITER", "1000000")),
                        persistence_dir=(os.environ.get("OH_PERSIST_DIR") or None),
                        callbacks=[_sink])
    conv.send_message(prompt)
    # The Qwen FP8 endpoint occasionally returns malformed JSON ("Extra data ...") that litellm maps to a
    # NON-retryable BadRequestError and crashes the agent mid-edit. Re-run the conversation on such transient
    # glitches - it resumes from persisted state and re-attempts the failed LLM step (succeeds next time).
    _TRANSIENT = ("Extra data", "Expecting value", "Unterminated string", "BadRequestError",
                  "Timeout", "APIError", "ServiceUnavailable", "InternalServerError", "Connection")
    for _attempt in range(6):
        try:
            conv.run()
            break
        except Exception as _e:
            _m = str(_e)
            if _attempt < 5 and any(t in _m for t in _TRANSIENT):
                print("OH_RETRY attempt %d after transient: %s" % (_attempt + 1, _m[:140]), flush=True)
                time.sleep(5 * (_attempt + 1))
                continue
            raise
    print("OH_RUN_DONE")
except Exception as e:
    traceback.print_exc()
    print("OH_RUN_ERROR", e)
    sys.exit(1)
