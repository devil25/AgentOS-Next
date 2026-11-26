"""Fixed, read-only DOM inspection for supported web-chat provider pages.

Unlike evaluate_script, this capability accepts no caller-supplied JavaScript.
It returns only visible assistant message text and page readiness metadata.
"""
from __future__ import annotations

WEB_CHAT_INSPECTION = r"""function() {
  const providers = {
    'chatgpt.com': {id:'chatgpt', messages:['[data-message-author-role="assistant"]'], composers:['#prompt-textarea','[contenteditable="true"][role="textbox"]','textarea']},
    'chat.openai.com': {id:'chatgpt', messages:['[data-message-author-role="assistant"]'], composers:['#prompt-textarea','[contenteditable="true"][role="textbox"]','textarea']},
    'gemini.google.com': {id:'gemini', messages:['model-response .response-container'], composers:['rich-textarea [contenteditable="true"]','[contenteditable="true"][role="textbox"]','textarea']},
    'chat.deepseek.com': {id:'deepseek', messages:['[data-message-author-role="assistant"]'], composers:['textarea','[contenteditable="true"][role="textbox"]']},
    'chat.z.ai': {id:'zai', messages:['[data-message-author-role="assistant"]'], composers:['textarea','[contenteditable="true"][role="textbox"]']}
  };
  const url = location.href, config = providers[location.hostname.toLowerCase()];
  if (location.protocol !== 'https:' || !config) return {url, blockedReason:'Unsupported provider origin.'};
  const visible = el => {
    if (!el || !el.isConnected) return false;
    const r=el.getBoundingClientRect(), s=getComputedStyle(el);
    return r.width>0 && r.height>0 && s.visibility!=='hidden' && s.display!=='none' && s.opacity!=='0';
  };
  const messages=[], seen=new Set();
  for (const selector of config.messages) {
    for (const el of document.querySelectorAll(selector)) {
      if (!visible(el)) continue;
      const text=String(el.innerText || el.textContent || '').trim();
      if (!text) continue;
      const id=el.getAttribute('data-message-id') || el.getAttribute('data-testid') || el.id || '';
      // Never synthesize identity from text or list position.
      if (!id || seen.has(id)) continue;
      seen.add(id);
      messages.push({message_id:id,text,complete:true,role:'assistant'});
    }
    if (messages.length) break;
  }
  const composer=config.composers.map(s=>document.querySelector(s)).find(visible);
  const body=String(document.body?.innerText || '').slice(0,5000);
  const generating=!!document.querySelector('button[aria-label*="Stop"],button[data-testid*="stop"],button[aria-label*="停止"],[data-is-streaming="true"]');
  const blockedReason=/verify you are human|captcha|access denied|rate limit/i.test(body)
    ? 'The provider is presenting a challenge or rate limit. Resolve it in Chrome before continuing.' : '';
  const loginRequired=/sign in|log in|登录|登录后/i.test(body) && !composer;
  return {url,title:document.title,provider:config.id,messages,composerReady:!!(composer && !composer.disabled),
    generating,loginRequired,blockedReason};
}"""
