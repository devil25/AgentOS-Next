// Provider-aware observer. Runs in the page's isolated content-script world.
(() => {
  const host = location.hostname;
  const provider = host === 'chatgpt.com' || host === 'chat.openai.com' ? 'chatgpt'
    : host === 'gemini.google.com' ? 'gemini'
    : host === 'chat.deepseek.com' ? 'deepseek'
    : host === 'chat.z.ai' ? 'zai' : '';
  if (!provider) return;

  const visible = element => {
    if (!(element instanceof Element)) return false;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none'
      && style.visibility !== 'hidden' && style.opacity !== '0';
  };
  const textOf = element => String(element?.innerText || element?.textContent || '')
    .replace(/\u00a0/g, ' ').replace(/[ \t]+/g, ' ').trim().slice(0, 2500);

  function messageNodes() {
    if (provider === 'chatgpt') return Array.from(document.querySelectorAll('[data-message-author-role]'));
    if (provider === 'gemini') return Array.from(document.querySelectorAll('user-query,model-response,[data-message-id]'));
    return Array.from(document.querySelectorAll('[data-message-author-role],[data-role],[data-author],[data-message-id],[data-testid*="message"],[class*="message"]'));
  }

  function roleOf(node) {
    const source = node.matches('[data-message-author-role],[data-role],[data-author]')
      ? node : node.querySelector('[data-message-author-role],[data-role],[data-author],[data-sender]');
    const hint = [
      source?.getAttribute('data-message-author-role'), source?.getAttribute('data-role'),
      source?.getAttribute('data-author'), source?.getAttribute('data-sender'),
      source?.getAttribute('aria-label'), source?.getAttribute('data-testid'),
      source?.tagName, source?.className?.baseVal || source?.className
    ].join(' ').toLowerCase();
    if (/user|human|user-query/.test(hint)) return 'user';
    if (/assistant|model|response/.test(hint)) return 'assistant';
    return '';
  }

  function collectMessages() {
    const result = [];
    const seen = new Set();
    for (const node of messageNodes()) {
      if (!visible(node)) continue;
      const role = roleOf(node);
      if (!role) continue;
      const content = node.querySelector('[data-message-content],.markdown,.message-content,.model-response-text') || node;
      const text = textOf(content);
      if (!text) continue;
      const id = String(node.getAttribute('data-message-id') || node.getAttribute('data-testid') || node.id || '');
      const key = role + ':' + text;
      if (seen.has(key)) continue;
      seen.add(key);
      result.push({ message_id: id.slice(0, 200), role, text, complete: true });
    }
    return result.slice(-50);
  }

  let lastSignature = '';
  let lastChangeAt = Date.now();
  let timer = null;
  let sending = false;
  let dirty = true;

  function buildSnapshot() {
    const messages = collectMessages();
    const composer = Array.from(document.querySelectorAll('textarea,[contenteditable="true"],[role="textbox"]'))
      .find(element => visible(element) && !element.disabled && !element.readOnly);
    const passwordVisible = Array.from(document.querySelectorAll('input[type="password"]')).some(visible);
    const stopVisible = Array.from(document.querySelectorAll('button[data-testid*="stop"],button[aria-label*="Stop" i],[data-is-streaming="true"]'))
      .some(element => visible(element) && !element.disabled);
    const signature = JSON.stringify(messages.map(item => [item.role, item.text]));
    if (signature !== lastSignature) {
      lastSignature = signature;
      lastChangeAt = Date.now();
    }
    const stableForMs = Math.max(0, Date.now() - lastChangeAt);
    return {
      provider,
      url: location.href,
      title: document.title,
      messages,
      composerReady: Boolean(composer),
      generating: stopVisible,
      loginRequired: passwordVisible,
      stable: !stopVisible && stableForMs >= 1200,
      stableForMs
    };
  }

  async function publish() {
    if (sending || !dirty || document.visibilityState === 'prerender') return;
    sending = true;
    dirty = false;
    const snapshot = buildSnapshot();
    try {
      await chrome.runtime.sendMessage({ type: 'agentos:observation', snapshot });
    } catch {
      dirty = true;
    } finally {
      sending = false;
    }
  }

  function schedule() {
    dirty = true;
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => { timer = null; void publish(); }, 400);
  }

  const observer = new MutationObserver(schedule);
  if (document.documentElement) observer.observe(document.documentElement, {
    subtree: true, childList: true, characterData: true, attributes: true,
    attributeFilter: ['aria-label','aria-busy','data-is-streaming','disabled']
  });
  document.addEventListener('visibilitychange', schedule);
  window.addEventListener('pageshow', schedule);
  schedule();
  window.setInterval(() => {
    if (dirty || document.visibilityState === 'visible') {
      const snapshot = buildSnapshot();
      if (!snapshot.generating && snapshot.stable) {
        dirty = true;
        void publish();
      }
    }
  }, 1500);
})();
