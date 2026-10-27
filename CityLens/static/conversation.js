/* Tab cache for the server-owned transcript. Workflow state stays authoritative. */
(() => {
  const safeText = value => String(value || '').slice(0, 6000)
    .replace(/\bsk-[A-Za-z0-9_-]{12,}\b/g, '[密钥已隐藏]')
    .replace(/\bBearer\s+[A-Za-z0-9._~-]{12,}\b/gi, '[凭证已隐藏]')
    .replace(/(?<!\d)1[3-9]\d{9}(?!\d)/g, '[手机号]')
    .replace(/\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b/g, '[邮箱]');
  class Conversation {
    constructor(storage) { this.storage = storage; this.key = null; this.entries = []; }
    switchTo(key) {
      this.key = key;
      this.entries = [];
      if (!key) return;
      try {
        const stored = JSON.parse(this.storage.getItem(key) || '[]');
        if (!Array.isArray(stored)) return;
        this.entries = stored.slice(-40).filter(item => item && ['user', 'assistant', 'system'].includes(item.role))
          .map(item => ({ id: String(item.id), role: item.role,
            text: safeText(item.status === 'pending' ? '上一轮连接已中断，请重新发送。' : item.text),
            status: item.status === 'pending' ? 'interrupted' : 'done' }));
      } catch (_) { /* Storage may be unavailable. */ }
    }
    replace(entries) {
      this.entries = (Array.isArray(entries) ? entries : []).slice(-200)
        .filter(item => item && ['user', 'assistant', 'system'].includes(item.role))
        .map(item => ({ id: String(item.id), role: item.role, text: safeText(item.text),
          status: item.status === 'pending' ? 'interrupted' : item.status || 'done' }));
      this.save();
    }
    add(role, text, status = 'done') {
      const entry = { id: crypto.randomUUID(), role, text: safeText(text), status };
      this.entries.push(entry);
      this.entries = this.entries.slice(-200);
      this.save();
      return entry.id;
    }
    update(id, text, status = 'done') {
      const entry = this.entries.find(item => item.id === id);
      if (entry) { entry.text = safeText(text); entry.status = status; this.save(); }
    }
    bind(key) {
      const old = this.key;
      this.key = key;
      this.save();
      if (old && old !== key && old.endsWith(':draft')) {
        try { this.storage.removeItem(old); } catch (_) {}
      }
    }
    clear() {
      if (this.key) { try { this.storage.removeItem(this.key); } catch (_) {} }
      this.entries = [];
      this.key = null;
    }
    save() {
      if (this.key) { try { this.storage.setItem(this.key, JSON.stringify(this.entries.slice(-40))); } catch (_) {} }
    }
  }
  window.CityLensConversation = Conversation;
})();
