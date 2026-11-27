// AgentOS Browser Companion background mediator.
// It forwards bounded page observations to the authenticated loopback bridge.
// It never reads cookies, credentials, browser storage, or password fields.

let pairingPromise = null;

async function readPairing() {
  if (!pairingPromise) {
    pairingPromise = fetch(chrome.runtime.getURL('pairing.json'), { cache: 'no-store' })
      .then(response => {
        if (!response.ok) throw new Error('AgentOS pairing is unavailable');
        return response.json();
      })
      .then(data => {
        const port = Number(data.port);
        if (!Number.isInteger(port) || port < 1024 || port > 65535
          || typeof data.token !== 'string' || data.token.length < 32) {
          throw new Error('AgentOS pairing is invalid');
        }
        return { port, token: data.token };
      })
      .catch(error => {
        pairingPromise = null;
        throw error;
      });
  }
  return pairingPromise;
}

async function forwardObservation(snapshot) {
  const pairing = await readPairing();
  const response = await fetch(`http://127.0.0.1:${pairing.port}/v1/companion/event`, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${pairing.token}`,
      'Content-Type': 'application/json'
    },
    cache: 'no-store',
    body: JSON.stringify(snapshot)
  });
  if (!response.ok) throw new Error(`AgentOS bridge rejected observation (${response.status})`);
  return response.json();
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type !== 'agentos:observation' || !message.snapshot) return false;
  const senderUrl = String(sender?.url || '');
  if (!/^https:\/\/(chatgpt\.com|chat\.openai\.com|gemini\.googleapis\.com|gemini\.google\.com|chat\.deepseek\.com|chat\.z\.ai)(\/|$)/i.test(senderUrl)) {
    sendResponse({ ok: false, error: 'Unsupported provider origin' });
    return false;
  }
  forwardObservation(message.snapshot)
    .then(result => sendResponse({ ok: true, accepted: Boolean(result?.accepted) }))
    .catch(error => sendResponse({ ok: false, error: String(error?.message || error).slice(0, 180) }));
  return true;
});
