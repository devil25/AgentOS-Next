# Embedded Browser Architecture

This branch introduces a native Electron `WebContentsView` inside AgentOS-Next.

## First implementation slice

- The AgentOS renderer owns the browser toolbar and provider selection.
- Electron main owns the remote page in a separate `WebContentsView`.
- ChatGPT, Gemini, DeepSeek, and Z.ai each use a separate persistent Chromium session partition. Sign-in state remains in the local Electron session; credentials are not copied into AgentOS settings or backend storage.
- The remote page has no Node integration, uses context isolation and sandboxing, and receives no site permissions by default.
- Navigation is HTTPS-only. The renderer cannot supply arbitrary JavaScript.
- The initial read operation is a fixed, read-only DOM extraction that returns the page title, URL, visible text, and headings. It does not read cookies, local storage, session storage, passwords, or authorization headers.
- The backend exposes this through the registered `embedded_browser_read` observation tool. The Electron-to-backend bridge is loopback-only and bearer-authenticated.
- The AgentOS tool runner can also request a bounded interactive-element snapshot, click a snapshot index, fill a visible non-password text field, send a small allow-listed keyboard event, or navigate to an HTTPS URL. The Electron side implements fixed actions; it never accepts arbitrary JavaScript from the backend.

## Current scope and next integration boundary

This slice establishes the in-app browser and routes page reading plus bounded interactions through registered AgentOS tools. It does **not yet** make the embedded conversation itself the active web-chat reasoning provider; that requires a provider-specific conversation adapter and end-to-end testing. The existing Chrome-extension path remains separate and unchanged.

The next slice must route the existing web-chat adapter through the embedded view, with provider-specific message/composer extraction and completion detection. Every inspection and interaction must continue through the tool runner's policy, approval, journaling, and durable-run ownership checks. Do not add a second ungoverned execution path.

## Validation requirements

- Verify the Electron view lifecycle and renderer-measured bounds.
- Verify persistent sessions per provider and secure navigation.
- Verify page inspection is fixed/read-only and never exposes browser storage or secrets.
- Run frontend typecheck/build and Electron tests.
- Perform a packaged Windows smoke test before treating this as production-ready.
