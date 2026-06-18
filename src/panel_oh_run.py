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
import os, sys, traceback, time

workdir, prompt = sys.argv[1], sys.argv[2]
try:
    from openhands.sdk import LLM, Agent, Conversation, LocalWorkspace
    from openhands.tools.preset.default import get_default_tools
    from openhands.sdk.context.condenser import LLMSummarizingCondenser
    from pydantic import SecretStr

    base = os.environ["OC_BASE"]
    model = "openai/" + os.environ["OC_MODEL"]
    key = SecretStr(os.environ["OC_KEY"])
    _R = dict(num_retries=5, retry_min_wait=3, retry_max_wait=45)
    llm = LLM(model=model, base_url=base, api_key=key, usage_id="jmt-oh",
              max_output_tokens=32768, temperature=0.0, native_tool_calling=True, **_R)
    cond = LLM(model=model, base_url=base, api_key=key, usage_id="jmt-cond",
               max_output_tokens=4096, temperature=0.0, native_tool_calling=False, **_R)
    agent = Agent(llm=llm, tools=get_default_tools(enable_browser=False),
                  condenser=LLMSummarizingCondenser(llm=cond, max_size=40, keep_first=2))
    conv = Conversation(agent=agent, workspace=LocalWorkspace(working_dir=workdir),
                        max_iteration_per_run=int(os.environ.get("OH_MAX_ITER", "60")))
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
