# Embedded Browser Architecture

This branch introduces a native Electron `WebContentsView` inside AgentOS-Next.

## Integrated implementation

- The AgentOS renderer owns the browser toolbar and provider selection.
- Electron main owns the remote page in a separate `WebContentsView`.
- ChatGPT, Gemini, DeepSeek, and Z.ai each use a separate persistent Chromium session partition. Sign-in state remains in the local Electron session; credentials are not copied into AgentOS settings or backend storage.
- The remote page has no Node integration, uses context isolation and sandboxing, and receives no site permissions by default.
- Navigation is HTTPS-only. The renderer cannot supply arbitrary JavaScript.
- The initial read operation is a fixed, read-only DOM extraction that returns the page title, URL, visible text, and headings. It does not read cookies, local storage, session storage, passwords, or authorization headers.
- The backend exposes this through the registered `embedded_browser_read` observation tool. The Electron-to-backend bridge is loopback-only and bearer-authenticated.
- The AgentOS tool runner can also request a bounded interactive-element snapshot, click a snapshot index, fill a visible non-password text field, send a small allow-listed keyboard event, or navigate to an HTTPS URL. The Electron side implements fixed actions; it never accepts arbitrary JavaScript from the backend.
- A provider-aware conversation observation returns bounded user/assistant message text, message IDs, composer readiness, login-required state, and generation state. It does not return browser storage or credentials.
- When a web-chat provider is selected in the AgentOS composer, the matching embedded provider session is opened before the run starts. `WebChatLLMClient` now uses `EmbeddedWebChatBrowserPort` rather than the Chrome extension transport.
- The embedded port dispatches conversation reads, snapshots, text entry, and Enter through the active `ToolBatchRunner.execute_transport_tool`, retaining tool registry validation, policy, approval, journaling, durable operation checks, and explicit tool-scope enforcement.

## Current scope and remaining validation

The embedded conversation is now the selected web-chat reasoning provider when the user chooses ChatGPT, Gemini, DeepSeek, or Z.ai in the composer. The Chrome-extension browser path remains available separately for ordinary browser tools. The provider DOM extraction is intentionally conservative and needs live verification on each provider because their page structures can change. A provider whose composer or messages cannot be identified must fail closed without sending text.

## Validation requirements

- Verify the Electron view lifecycle and renderer-measured bounds.
- Verify persistent sessions per provider and secure navigation.
- Verify provider message extraction, composer selection, and completed-response detection against live signed-in sessions for all four providers.
- Verify one action cycle end-to-end: provider proposes one tool action, AgentOS executes it through the normal runner, and the real result is returned to the same provider conversation.
- Verify page inspection is fixed/read-only and never exposes browser storage or secrets.
- Run frontend typecheck/build and Electron tests.
- Perform a packaged Windows smoke test before treating this as production-ready.


## Login-once, in-app conversation workflow

The embedded provider browser is a sign-in and session-management surface, not the normal chat UI.
Users open it explicitly from the AgentOS app, choose a provider, and sign in. Closing the panel
hides the provider WebContentsView without destroying it, so the authenticated persistent Electron
session remains available to the loopback-only, bearer-authenticated backend bridge. Sending a
message from AgentOS never opens or foregrounds the browser; the selected provider is sent to the
backend conversation run, which reads and submits through the already attached provider session.
Reopening the browser panel for the same provider reuses the existing WebContentsView and does not
navigate away from the signed-in conversation. Switching providers creates/attaches the selected
provider's separate persistent session.

This workflow does not export cookies, passwords, local storage, or provider tokens. If the
provider session is unavailable or signed out, the AgentOS run must report that state and the user
can explicitly reopen the browser panel to sign in again.
