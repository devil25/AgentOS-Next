# Embedded Browser Architecture

This branch introduces a native Electron `WebContentsView` inside AgentOS-Next.

## First implementation slice

- The AgentOS renderer owns the browser toolbar and provider selection.
- Electron main owns the remote page in a separate `WebContentsView`.
- ChatGPT, Gemini, DeepSeek, and Z.ai each use a separate persistent Chromium session partition. Sign-in state remains in the local Electron session; credentials are not copied into AgentOS settings or backend storage.
- The remote page has no Node integration, uses context isolation and sandboxing, and receives no site permissions by default.
- Navigation is HTTPS-only. The renderer cannot supply arbitrary JavaScript.
- The initial read operation is a fixed, read-only DOM extraction that returns the page title, URL, visible text, and headings. It does not read cookies, local storage, session storage, passwords, or authorization headers.
- The backend exposes this through the registered `embedded_browser_read` observation tool. The Electron-to-backend bridge is loopback-only, bearer-authenticated, and accepts only the fixed inspection route; the normal agent tool runner invokes the read tool.

## Current scope and next integration boundary

This slice establishes the in-app browser and routes read-only page inspection to the agent through its normal registered tool path. It does **not yet** provide browser interaction tools for the embedded view or make the embedded conversation itself the active web-chat reasoning provider. The existing Chrome-extension path remains separate and unchanged.

The next slice must add controlled interaction tools (snapshot, fill, key, click, navigation) and route the existing web-chat adapter through the embedded view. Every inspection and interaction must continue through the tool runner's policy, approval, journaling, and durable-run ownership checks. Do not add a second ungoverned execution path.

## Validation requirements

- Verify the Electron view lifecycle and renderer-measured bounds.
- Verify persistent sessions per provider and secure navigation.
- Verify page inspection is fixed/read-only and never exposes browser storage or secrets.
- Run frontend typecheck/build and Electron tests.
- Perform a packaged Windows smoke test before treating this as production-ready.
