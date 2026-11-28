// Provider-aware event observer. Runs in the page's isolated content-script world.
// It observes rendered UI only; it never reads credentials, cookies, or storage.
(() => {
  const host = location.hostname;
  const provider = host === 'chatgpt.com' || host === 'chat.openai.com' ? 'chatgpt'
    : host === 'gemini.google.com' ? 'gemini'
    : host === 'chat.deepseek.com' ? 'deepseek'
    : host === 'chat.z.ai' ? 'zai' : '';
  if (!provider) return;

  const documentId = (() => {
    try { return crypto.randomUUID(); } catch { return `agentos-${Date.now()}-${Math.random().toString(36).slice(2)}`; }
  })();
  const nodeIds = new WeakMap();
  let nextNodeId = 0;
  let sequence = 0;
  let lastPublishedSignature = '';
  let lastObservedSignature = '';
  let lastPublishedAt = 0;
  let lastChangeAt = Date.now();
  let timer = null;
  let sending = false;
  let dirty = true;

  const visible = element => {
    if (!(element instanceof Element)) return false;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none'
      && style.visibility !== 'hidden' && style.opacity !== '0';
  };
  const textOf = element => String(element?.innerText || element?.textContent || '')
    .replace(/\u00a0/g, ' ').replace(/[ \t]+/g, ' ').trim().slice(0, 2500);
  const stableId = node => {
    const supplied = node.getAttribute('data-message-id') || node.getAttribute('data-testid')
      || node.getAttribute('data-test-id') || node.id;
    if (supplied) return String(supplied).slice(0, 200);
    if (!nodeIds.has(node)) nodeIds.set(node, `agentos-${provider}-${++nextNodeId}`);
    return nodeIds.get(node);
  };

  const CHATGPT_MESSAGE_SELECTORS = [
    '[data-message-author-role]',
    '[data-role="user"]', '[data-role="assistant"]',
    '[data-author="user"]', '[data-author="assistant"]',
    '[data-message-role="user"]', '[data-message-role="assistant"]',
    '[data-author-role="user"]', '[data-author-role="assistant"]',
    '[data-turn-role="user"]', '[data-turn-role="assistant"]',
    '[data-message-author="user"]', '[data-message-author="assistant"]',
    '[data-conversation-role="user"]', '[data-conversation-role="assistant"]',
    '[data-turn="user"]', '[data-turn="assistant"]',
    '[data-testid^="conversation-turn-"][data-turn="user"]',
    '[data-testid^="conversation-turn-"][data-turn="assistant"]',
    '[data-test-id^="conversation-turn-"][data-turn="user"]',
    '[data-test-id^="conversation-turn-"][data-turn="assistant"]',
    'section[data-turn="user"]', 'section[data-turn="assistant"]',
    '[data-chatgpt-search-unit-key$=":user"]', '[data-chatgpt-search-unit-key$=":assistant"]',
    '[data-content-search-unit-key$=":user"]', '[data-content-search-unit-key$=":assistant"]',
    '[data-markdown-text-style="assistant-message"]'
  ].join(',');

  function messageNodes() {
    if (provider === 'chatgpt') return Array.from(document.querySelectorAll(CHATGPT_MESSAGE_SELECTORS));
    if (provider === 'gemini') return Array.from(document.querySelectorAll('user-query,model-response,[data-message-id]'));
    return Array.from(document.querySelectorAll('[data-message-author-role],[data-role],[data-author],[data-message-id],[data-testid*="message"],[class*="message"]'));
  }

  function roleOf(node) {
    const marker = '[data-message-author-role],[data-role],[data-author],[data-sender],[data-turn],[data-turn-role],[data-author-role],[data-message-author],[data-conversation-role],[data-chatgpt-search-unit-key],[data-content-search-unit-key],[data-markdown-text-style]';
    const source = node.matches(marker) ? node : node.querySelector(marker) || node;
    const hint = [
      source?.getAttribute('data-message-author-role'), source?.getAttribute('data-role'),
      source?.getAttribute('data-author'), source?.getAttribute('data-sender'),
      source?.getAttribute('data-turn'), source?.getAttribute('data-turn-role'),
      source?.getAttribute('data-author-role'), source?.getAttribute('data-message-author'),
      source?.getAttribute('data-conversation-role'), source?.getAttribute('data-chatgpt-search-unit-key'),
      source?.getAttribute('data-content-search-unit-key'), source?.getAttribute('data-markdown-text-style'),
      source?.getAttribute('aria-label'), source?.getAttribute('data-testid'), source?.getAttribute('data-test-id'),
      source?.tagName, source?.className?.baseVal || source?.className
    ].filter(Boolean).join(' ').toLowerCase();
    if (/user|human|user-query/.test(hint)) return 'user';
    if (/assistant|model|response/.test(hint)) return 'assistant';
    return '';
  }

  function isGenerating(nodes) {
    const stopVisible = Array.from(document.querySelectorAll(
      'button[data-testid*="stop"],button[aria-label*="Stop" i],button[aria-label*="Stop generating" i],[data-testid*="stop-generating"]'
    )).some(element => visible(element) && !element.disabled);
    const streamingMessage = nodes.some(node =>
      node.getAttribute('data-is-streaming') === 'true'
      || Boolean(node.querySelector('[data-is-streaming="true"]'))
    );
    return stopVisible || streamingMessage;
  }

  function collectMessages(nodes, generating, stable) {
    const result = [];
    const seen = new Set();
    for (const node of nodes) {
      if (!visible(node)) continue;
      const role = roleOf(node);
      if (!role) continue;
      const content = node.querySelector('[data-message-content],.markdown,.message-content,.model-response-text') || node;
      const text = textOf(content);
      if (!text) continue;
      const id = stableId(node);
      const key = role + ':' + id;
      if (seen.has(key)) continue;
      seen.add(key);
      result.push({ message_id: id, role, text, complete: true });
    }
    const lastAssistantIndex = result.findLastIndex(item => item.role === 'assistant');
    if (lastAssistantIndex >= 0 && (generating || !stable)) result[lastAssistantIndex].complete = false;
    return result.slice(-50);
  }

  function buildSnapshot() {
    const nodes = messageNodes();
    const generating = isGenerating(nodes);
    const composer = Array.from(document.querySelectorAll('textarea,[contenteditable="true"],[role="textbox"]'))
      .find(element => visible(element) && !element.disabled && !element.readOnly);
    const passwordVisible = Array.from(document.querySelectorAll('input[type="password"]')).some(visible);
    const messages = collectMessages(nodes, generating, false);
    const signature = JSON.stringify({
      url: location.href,
      messages: messages.map(item => [item.message_id, item.role, item.text]),
      generating, composerReady: Boolean(composer), loginRequired: passwordVisible
    });
    if (signature !== lastObservedSignature) {
      lastObservedSignature = signature;
      lastChangeAt = Date.now();
    }
    const stableForMs = Math.max(0, Date.now() - lastChangeAt);
    const stable = !generating && stableForMs >= 900;
    const completeMessages = collectMessages(nodes, generating, stable);
    const lastAssistant = completeMessages.findLast(item => item.role === 'assistant');
    const eventType = generating ? 'assistant_streaming'
      : lastAssistant && lastAssistant.complete ? 'assistant_completed'
      : 'conversation_changed';
    const alerts = Array.from(document.querySelectorAll('[role="alert"],[data-testid*="error"],[data-testid*="warning"]'))
      .filter(visible).map(textOf).filter(Boolean).join(' ').slice(0, 500);
    const lowerAlerts = alerts.toLowerCase();
    const blockedReason = /message is too long|too many tokens|rate limit|try again later|captcha|verify you are human|message too long|الرسالة طويلة|حاول مرة أخرى/.test(lowerAlerts)
      ? alerts : '';
    return {
      provider, documentId, url: location.href, title: document.title,
      messages: completeMessages, composerReady: Boolean(composer), generating,
      loginRequired: passwordVisible, blockedReason, stable, stableForMs,
      eventType, observedAt: Date.now()
    };
  }

  async function publish() {
    if (sending || !dirty || document.visibilityState === 'prerender') return;
    sending = true;
    dirty = false;
    const snapshot = buildSnapshot();
    const signature = JSON.stringify({
      url: snapshot.url, messages: snapshot.messages, generating: snapshot.generating,
      stable: snapshot.stable, composerReady: snapshot.composerReady,
      loginRequired: snapshot.loginRequired, blockedReason: snapshot.blockedReason
    });
    const now = Date.now();
    if (signature === lastPublishedSignature && now - lastPublishedAt < 5000) {
      sending = false;
      return;
    }
    snapshot.sequence = ++sequence;
    try {
      const result = await chrome.runtime.sendMessage({ type: 'agentos:observation', snapshot });
      if (result?.ok === false) dirty = true;
      else {
        lastPublishedSignature = signature;
        lastPublishedAt = now;
      }
    } catch {
      dirty = true;
    } finally {
      sending = false;
    }
  }

  function schedule() {
    dirty = true;
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => { timer = null; void publish(); }, 120);
  }

  const observer = new MutationObserver(schedule);
  if (document.documentElement) observer.observe(document.documentElement, {
    subtree: true, childList: true, characterData: true, attributes: true,
    attributeFilter: ['aria-label','aria-busy','data-is-streaming','disabled','data-message-id','data-testid']
  });
  document.addEventListener('visibilitychange', schedule);
  window.addEventListener('pageshow', schedule);
  window.addEventListener('popstate', schedule);
  window.addEventListener('hashchange', schedule);
  schedule();
  window.setInterval(() => {
    if (document.visibilityState !== 'prerender') {
      const snapshot = buildSnapshot();
      const signature = JSON.stringify({
        url: snapshot.url, messages: snapshot.messages, generating: snapshot.generating,
        stable: snapshot.stable, composerReady: snapshot.composerReady,
        loginRequired: snapshot.loginRequired, blockedReason: snapshot.blockedReason
      });
      if (signature !== lastPublishedSignature || Date.now() - lastPublishedAt >= 5000) schedule();
    }
  }, 1000);
})();
