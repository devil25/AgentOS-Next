# Web-chat providers as the AgentOS reasoning engine

## Product requirement

AgentOS-Next must support using the **signed-in web conversation UI** of AI platforms as the reasoning engine. The model website plans and emits the next instruction; AgentOS-Next owns the execution runtime, permissions, browser/computer tools, files, terminal, skills, MCP integrations, durable task state, and verification.

This is distinct from calling a provider's API. API keys, native SDKs, and local model endpoints may remain optional provider modes, but they are not the primary implementation of this requirement.

Initial web-chat targets:
- ChatGPT at `chatgpt.com`
- Gemini at `gemini.google.com`
- DeepSeek at `chat.deepseek.com`
- Z.ai at `chat.z.ai`

Chrome is the first supported browser. Firefox is a later, separate browser transport adapter.

## Core interaction loop

1. The user selects a web-chat provider and opens its website in the paired Chrome profile. Unless a page ID is explicitly supplied, AgentOS-Next calls the existing read-only `list_pages` tool, matches HTTPS origins to the selected provider, and automatically selects the only matching tab. If multiple matching tabs exist, it stops with their page IDs/titles rather than guessing. The user signs in directly on the provider website; AgentOS-Next must not collect, store, or ask for provider passwords, cookies, or session tokens.
2. AgentOS-Next creates or resumes a task and builds a compact instruction containing the original user goal, relevant task context, the allowed AgentOS action protocol, and the last verified execution results.
3. The selected website receives that instruction in its own conversation. The adapter waits for a **new assistant response** and must distinguish it from the user's prompt, prior assistant messages, page chrome, and stale DOM content.
4. The response adapter extracts the assistant's actual message. The model must return either a final user-facing answer or exactly one fenced `agentos.v1` action envelope, for example:

   ```json
   {"protocol":"agentos.v1","type":"action","action":"workspace.create_file","arguments":{"path":"result.txt","content":"..."}}
   ```

5. AgentOS-Next validates the envelope against the registered action/tool schema, permission policy, workspace boundaries, URL policy, approval requirements, and execution state. The model website never executes local tools directly.
6. AgentOS-Next executes the action using its existing runtime and records the real result. It sends that result back into the **same provider conversation** as the next turn.
7. Repeat until the provider returns a final answer, the user pauses/stops, approval is required, or a recoverable error occurs. Completion is recorded only after the task's completion conditions and evidence checks pass.

## Architecture boundaries

### WebChatProvider adapter

A provider adapter owns only website-specific behavior:
- Supported origin and URL validation.
- Finding the intended conversation tab: auto-discover a unique matching provider tab via `list_pages`, or stop with an ambiguity message when multiple tabs match. Never guess between multiple conversations.
- Locating the composer and send control.
- Submitting text without changing the active conversation unexpectedly.
- Detecting a newly-created assistant response and its completion state.
- Extracting only that assistant response, including fenced action text.
- Detecting login-required, rate-limit, challenge, unsupported-layout, and transient-failure states.
- Provider-specific selectors and UI version diagnostics.

Adapters must not contain tool execution policy or perform workspace/terminal actions.

### Browser transport

Use the existing Chrome extension + authenticated loopback WebSocket + DevTools relay as the browser transport where practical. Reuse its pairing, tab/session tracking, snapshots, and verified interaction primitives. Add a narrow web-chat session service above the transport rather than duplicating CDP or introducing a second uncontrolled browser.

Do not scrape provider internal APIs, reverse-engineer private endpoints, or bypass login, paywalls, CAPTCHAs, or service restrictions. Operate through the visible, user-authenticated website UI.

### AgentOS reasoning/execution coordinator

Introduce a reasoning-provider contract that can be implemented by:
- Web-chat UI providers (primary requirement).
- Existing API providers (optional mode).
- Local model providers (optional mode).

The coordinator must be independent of the transport. It owns task continuity, action parsing, schema validation, execution dispatch, result handoff, retries, pause/resume, and completion verification.

The existing MonaW turn loop, tool registry, durable runs, approval broker, execution gate, and event journal should be reused where they fit. Do not create a parallel unsafe tool executor.

### AgentOS process and browser bridge ownership

The current Chrome extension bridge is not owned by the FastAPI application process. `backend/app/skills/mcp_bridge/builtin.py` launches `python -m app.skills.browser_use.extension` as a separate MCP child process; that child owns the loopback WebSocket listener and the extension connection. Consequently, constructing a second `ExtensionBridge` inside `AgentRuntime` or `ChromeWebChatBrowserPort` on the same configured port would compete with the MCP child and is not a valid integration. The first vertical slice must route web-chat page operations through the already-managed `chrome-devtools` MCP connection (or deliberately refactor ownership so one lifecycle-managed bridge is shared), while keeping page interaction narrowly scoped to the supported provider origins. Do not start a second listener or duplicate extension pairing.

The web-chat provider must also be selected per task/run, not by globally replacing `LLMClient`: API/local providers remain available as alternatives, and all web-chat turns must still enter the existing `TurnLoop` so tool execution, approvals, durable journaling, and verification remain authoritative.

### Integration boundary and current blocker

The `chrome-devtools` MCP server is a child process launched by `mcp_bridge/builtin.py`; its `ExtensionBridge` and WebSocket connection live in that child. A backend web-chat adapter must not instantiate another bridge on the same port. `ChromeWebChatBrowserPort` now binds per run to the active `ToolBatchRunner`. Its internal inspect/fill/submit calls pass through the existing registry validation, ToolPolicy, ToolExecutor, ExecutionGateService, approval flow, durable operation journal, and observability. The chat API now accepts a paired `web_chat_provider` and explicit `web_chat_page_id`, and composes the `WebChatLLMClient` for that run. Durable Run context stores only the provider, Chrome page ID, and validated conversation URL so a paused Run can restore the same reasoning website and conversation; it never stores browser credentials. A separate per-Run action-ID table reserves each provider-generated action ID once and stores only the tool name and a SHA-256 digest of its arguments. This prevents the same action ID from being dispatched again after a process restart or Run resume; an interrupted reservation is treated conservatively as already used. The desktop UI still needs provider/page discovery and selection, and a stale page ID after Chrome restarts must fail safely rather than switching pages. Until those are implemented and exercised against live provider sites, web-chat remains a development integration, not an end-user-ready feature.

A further implementation constraint has been confirmed in the shipped Chrome tool policy: `evaluate_script` is explicitly classified as arbitrary page-code execution with high risk and always-confirm behavior. The earlier DOM prototype used it for both polling and message submission; that approach was rejected because it would create repeated approval prompts and undermine the supervised UX. The current web-chat port no longer uses that capability. The ordinary `take_snapshot`, `inspect_element`, and `wait_for` tools are read-only and do not require that script capability, while `fill`, `click`, and `type_text` retain their normal interaction gates. A fixed `inspect_web_chat` browser capability has now been added. It accepts only an explicit pageId, runs a built-in read-only inspection function (not caller-supplied JavaScript), limits results to visible assistant messages with provider-supplied stable DOM IDs, and reports composer/login/challenge/generation state. Its MCP policy is read-only. The web-chat port calls registered `inspect_web_chat`, `take_snapshot`, `fill`, and `press_key` tools through the per-run executor bound by `TurnLoop` to the active `ToolBatchRunner`; it no longer sends arbitrary page functions or uses `evaluate_script`. Submission refuses ambiguous composer snapshots before entering text. The request field is a Chrome DevTools `pageId` (not a tab index). Desktop provider/page selection, durable selected-page binding, and live provider layout validation remain outstanding. Do not weaken the existing `evaluate_script` policy to make the prototype work.

## Current backend entry point

The desktop chat request has an optional web-chat reasoning target:
- `web_chat_provider`: one of `chatgpt`, `gemini`, `deepseek`, or `zai`.
- `web_chat_page_id`: the explicit numeric Chrome DevTools `pageId` returned by the browser's `list_pages` tool.

Both fields must be supplied together. When present, the chat route constructs a per-run `WebChatLLMClient` and passes it to `run_agent_stream`; TurnLoop then binds the browser port to that run's `ToolBatchRunner`. No provider credentials are requested or stored. This is an internal API integration seam until the desktop UI can discover and select pages safely.

## Action protocol requirements

- Accept only one `agentos.v1` action envelope per assistant response.
- Reject malformed JSON, multiple action blocks, unknown actions, unknown arguments, and out-of-scope paths.
- Treat all website output as untrusted model output. It cannot change AgentOS policy or grant itself permissions.
- Require explicit user approval for actions that existing AgentOS policy marks as sensitive.
- Never infer success from the model's claim. Use the actual tool result and independent verification.
- Include a stable task/run ID, step ID, action ID, and idempotency identity so retries and context handoff do not repeat completed mutations.
- If the assistant returns ordinary prose when an action is required, report a protocol error and recover within the same conversation; do not mark the task complete.
- Preserve the original user goal and a compact execution ledger across context limits, page reloads, provider errors, and pause/resume.

## Session and continuity requirements

Persist locally:
- Provider ID and conversation URL/identifier (not provider credentials).
- Browser tab identity while available, with safe rediscovery after restart.
- Task's original goal, current phase, last completed action, verified result, and pending action.
- A bounded transcript/summary of instructions and tool results handed to the provider.

Do not silently switch provider conversations. If the original conversation cannot be recovered, pause and ask the user to choose or re-establish it.

## Runtime integration boundary

The runtime now accepts an optional per-run `reasoning_client` and threads it
through the existing `AgentRuntime -> TurnLoop -> ModelRequestRunner` path.
When supplied, it is used only for reasoning requests and provider-specific tool
result messages for that turn. The shared tool registry, ToolBatchRunner,
ToolExecutor, ExecutionGateService, approval/access-grant handling, durable
operation journal, and turn lifecycle remain owned by the existing runtime.
Execution-gate review calls are deliberately kept on the runtime's configured
native LLM client, not the web-chat reasoning client, so approval/review prompts
cannot silently create extra turns in the user's provider conversation.
When omitted, the configured API/local LLM client remains unchanged.

This remains an integration seam, not a user-facing provider feature. TurnLoop
now binds a WebChatLLMClient's Chrome port to the active ToolBatchRunner for
internal browser transport. A production setup still needs to resolve the
authorized selected Chrome page, create the provider adapter for that page,
persist the task-to-page/conversation binding, and supply the client to the run
entry point. No UI setting should be exposed until that lifecycle and tab
authorization are implemented and tested end-to-end.

## Conversation binding and tab-switch safety

A web-chat task is bound to the selected Chrome tab and, after the first successful
assistant response, to the conversation URL identity (HTTPS hostname plus path).
Query strings and fragments may change without changing the conversation identity.
Every subsequent turn must verify the same identity before sending and while polling.
A tab that is switched to another conversation on the same provider must stop the
task before another prompt is submitted. The first prompt may cause a provider to
create a conversation path, so the initial path is captured from the first completed
turn and pinned for all follow-up turns. No conversation credentials are stored.

## Security and privacy

- Provider authentication remains in the user's browser profile.
- Never read or export cookies, local storage tokens, passwords, or provider session secrets.
- Limit web-chat interaction to the explicit supported provider origins.
- Treat page text, model output, and website content as untrusted input.
- Keep AgentOS permission checks, approvals, audit logs, and execution verification authoritative.
- Clearly distinguish website UI compatibility from official provider API support; do not promise that a free plan, model, or feature is available unless verified.

## Implementation status on the AgentOS-Next development branch

The first reusable backend layer has now been added:
- `protocol.py`: exact HTTPS provider-origin matching, strict single-action JSON parsing, registered-action allowlisting, bounded payloads, and authoritative tool-result formatting.
- `adapter.py`: provider-neutral session orchestration that snapshots the existing assistant message IDs, submits one prompt, ignores old replies, rejects login/blocked states, and waits for a new stable completed assistant response.
- `chrome_port.py`: visible-page DOM adapter that accepts an injected page executor. It no longer constructs an `ExtensionBridge`, starts a competing WebSocket listener, or owns browser pairing/process lifecycle. The executor must be supplied by the application through the existing authorized `chrome-devtools` tool path; it does not access cookies, local storage, provider tokens, or hidden API endpoints. Provider-specific DOM selectors are provisional until live browser tests.
- `coordinator.py`: bounded action/result loop that validates action arguments against the supplied AgentOS tool schemas and delegates execution to a caller-supplied **existing policy-gated** dispatcher. It does not invoke tools or grant permissions itself.
- `llm_client.py`: an `LLMClient`-compatible client that converts web-chat actions into the existing `ToolCallRequest` shape. This lets the established `TurnLoop` / `ToolBatchRunner` remain responsible for tool execution and approvals. `AgentRuntime` now accepts an injected reasoning client as a composition point.
- Focused unit tests cover origin matching, malformed/ambiguous responses, stale/partial response isolation, schema rejection, and multi-step result handoff.

**Integration status:** the web-chat reasoning client is now wired into the normal chat and detached-job entry points. The chat composer selects one of ChatGPT, Gemini, DeepSeek, or Z.ai and an explicit Chrome DevTools page ID; the request schema requires the provider and page ID together. The selected provider/page binding and conversation host/path are persisted with the durable Run, without query strings, fragments, or credentials, and are restored for continuation. TurnLoop binds the Chrome port to the active `ToolBatchRunner` transport. Initial inspection uses the fixed read-only `inspect_web_chat` capability; prompt entry uses the existing snapshot/fill/keyboard tools. These calls remain subject to the normal registry, policy, execution gate, approval, journaling, and observability paths. Tool results are tagged in the shared transcript so the web-chat client can return the actual result to the same provider conversation rather than replaying the original request. Security-review calls remain on the configured native review client. **Live validation remains outstanding:** provider-specific DOM selectors are provisional, and no provider is production-ready until its signed-in Chrome workflow passes the end-to-end acceptance tests below.

## Delivery plan

1. **Foundation:** define the provider contract, action-envelope parser/validator, task handoff records, and browser-tab/session ownership.
2. **First vertical slice:** implement one website adapter end-to-end (ChatGPT UI), with deterministic DOM fixtures and real-browser tests for prompt submission, new-response detection, action extraction, tool result handoff, pause/resume, and stale-response rejection.
3. **Provider adapters:** add Gemini, DeepSeek, and Z.ai one at a time using the same contract and provider-specific UI tests.
4. **Reliability:** harden timeouts, retries, duplicate-action prevention, response truncation, context handoff, layout changes, and recovery.
5. **Optional API/local providers:** retain as alternate modes without making them prerequisites for web-chat use.
6. **Firefox:** add only after Chrome web-chat workflows are stable, behind a separate browser transport adapter.

## Acceptance criteria

A provider is not considered supported merely because its website can be opened. For each provider, tests must prove:
- The user's existing signed-in session can be used without credentials being copied into AgentOS.
- The task is sent to the selected conversation.
- Only the new assistant response is parsed.
- A valid action envelope triggers the matching AgentOS tool once.
- The real tool result is sent back to that same conversation.
- The provider can request a second action based on the result.
- Ordinary prose, stale content, malformed envelopes, and unsupported layouts never trigger tools or false completion.
- Stop, pause, resume, and browser disconnection preserve task state safely.

No provider is declared production-ready until its end-to-end Chrome tests pass against the live website UI and the supported interaction flow is documented.

## Event-driven embedded browser observation and long-running turns

The embedded Browser Companion now publishes sequenced observations when the rendered provider UI changes. Each observation is bound to a per-document identifier, a monotonic sequence number, the active HTTPS provider origin and conversation path. The Electron loopback bridge rejects stale/out-of-order events and observations from another conversation route. Observations distinguish assistant streaming from a stable completed reply; the final assistant message remains marked incomplete while generation is active or the content is still settling.

Submitting a ChatGPT prompt is acknowledged as soon as the visible Send action/native Enter action is issued. The send IPC does not wait for the provider's response. The pending prompt identity is retained until the companion observation proves the matching user turn and a subsequent completed assistant reply. This separates the short input action from the potentially long model-generation phase.

The web-chat adapter has no default response deadline or empty-generation cutoff. A caller may explicitly configure a deadline, while normal AgentOS pause/stop cancellation remains available. Read-only browser observations and task-plan refreshes may be repeated; state-changing actions such as Enter, click, type, and local execution remain protected against accidental duplicate side effects. In particular, an uncertain filesystem or opaque write is not made repeat-safe merely to increase model freedom.

Playwright is already part of the frontend development toolchain. It is used to exercise Electron/Chromium behavior against controlled provider-page fixtures, including observer streaming/completion transitions. This does not introduce a second browser session into the user's live signed-in workflow. Runtime browser interaction continues through the existing embedded Electron WebContentsView and authenticated Companion bridge; no public CDP debugging port or second ExtensionBridge is introduced.
