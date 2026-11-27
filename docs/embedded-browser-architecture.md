# Embedded Browser Architecture

This branch introduces a native Electron `WebContentsView` inside AgentOS-Next.

## First implementation slice

- The AgentOS renderer owns the browser toolbar and provider selection.
- Electron main owns the remote page in a separate `WebContentsView`.
- ChatGPT, Gemini, DeepSeek, and Z.ai each use a separate persistent Chromium session partition. Sign-in state remains in the local Electron session; credentials are not copied into AgentOS settings or backend storage.
- The remote page has no Node integration, uses context isolation and sandboxing, and receives no site permissions by default.
- Navigation is HTTPS-only. The renderer cannot supply arbitrary JavaScript.
- The initial read operation is a fixed, read-only DOM extraction that returns the page title, URL, visible text, and headings. It does not read cookies, local storage, session storage, passwords, or authorization headers.

## Current scope and next integration boundary

This first slice establishes the in-app browser and a direct AgentOS-owned page-inspection path. It does **not yet** route the embedded page into the agent's durable browser tool runner or make the embedded conversation the active reasoning provider. The existing Chrome-extension path remains separate and unchanged.

The next slice must add an authenticated loopback bridge between the backend tool runner and this embedded `WebContentsView`, then route the existing web-chat adapter through that bridge. Every inspection and interaction must continue through the tool runner's policy, approval, journaling, and durable-run ownership checks. Do not add a second ungoverned execution path.

## Validation requirements

- Verify the Electron view lifecycle and renderer-measured bounds.
- Verify persistent sessions per provider and secure navigation.
- Verify page inspection is fixed/read-only and never exposes browser storage or secrets.
- Run frontend typecheck/build and Electron tests.
- Perform a packaged Windows smoke test before treating this as production-ready.
