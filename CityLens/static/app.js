const input = document.querySelector('#message');
const answer = document.querySelector('#answer');
const results = document.querySelector('#results');
const plans = document.querySelector('#plans');
const comparison = document.querySelector('#comparison');
const constraints = document.querySelector('#constraints');
const relaxations = document.querySelector('#relaxations');
const decisions = document.querySelector('#decisions');
const trace = document.querySelector('#trace');
const traceDialog = document.querySelector('#trace-dialog');
const traceToggle = document.querySelector('#open-trace');
function openTrace() {
  if (trace.hidden || !authenticated) return;
  if (!traceDialog.open) traceDialog.showModal();
}
function resetTrace() {
  trace.hidden = true;
  traceToggle.disabled = true;
  trace.querySelector('ol').replaceChildren();
  trace.querySelector('#run-id').textContent = '';
  if (traceDialog.open) traceDialog.close();
}
traceToggle.onclick = openTrace;
document.querySelector('#trace-close').onclick = () => traceDialog.close();
const send = document.querySelector('#send');
const authStatus = document.querySelector('#auth-status');
const authFields = document.querySelector('#auth-fields');
const authTitle = document.querySelector('#auth-title');
const logoutButton = document.querySelector('#logout');
const accountDialog = document.querySelector('#account-dialog');
const accountToggle = document.querySelector('#account-toggle');
function openAccount() {
  if (!accountDialog.open) accountDialog.showModal();
}
accountToggle.onclick = openAccount;
document.querySelector('#open-login').onclick = openAccount;
document.querySelector('#account-close').onclick = () => accountDialog.close();
accountDialog.addEventListener('close', () => { document.querySelector('#code').value = ''; });
document.querySelector('#code').addEventListener('keydown', event => {
  if (event.key === 'Enter') { event.preventDefault(); document.querySelector('#login').click(); }
});
let coordinates = null;
let sessionId = null;
let sessionKey = null;
let authenticated = false;
let authGeneration = 0;
let pending = null;
let activeRequestId = null;
let requestNumber = 0;
let currentRunId = null;
let lastPlan = null;
let selectedActionId = null;
let selectedActionFree = false;
let actionRequestNumber = 0;
const actionDialog = document.querySelector('#action-dialog');
const actionStatus = document.querySelector('#action-status');
const memorySummary = document.querySelector('#memory-summary');
const historyList = document.querySelector('#history-list');
const actionHistory = document.querySelector('#action-history');
const taskHistory = document.querySelector('#task-history');
send.disabled = true;

const conversation = new window.CityLensConversation({
  getItem: key => sessionStorage.getItem(key),
  setItem: (key, value) => sessionStorage.setItem(key, value),
  removeItem: key => sessionStorage.removeItem(key),
});
const messageResults = new Map();
let pendingChatId = null;
const chatLog = document.querySelector('#conversation-log');
function conversationKey() { return sessionKey ? `citylens-chat:${sessionKey}:${sessionId || 'draft'}` : null; }
function renderConversation() {
  const nearBottom = chatLog.scrollHeight - chatLog.scrollTop - chatLog.clientHeight < 80;
  const ids = new Set(conversation.entries.map(entry => `chat-${entry.id}`));
  for (const id of messageResults.keys()) if (!ids.has(`chat-${id}`)) messageResults.delete(id);
  for (const child of [...chatLog.children]) if (!ids.has(child.id)) child.remove();
  for (const entry of conversation.entries) {
    const id = `chat-${entry.id}`;
    let node = document.getElementById(id);
    if (!node) {
      node = document.createElement('article'); node.id = id;
      const speaker = document.createElement('p'); speaker.className = 'speaker';
      const bubble = document.createElement('div'); bubble.className = 'bubble';
      node.append(speaker, bubble); chatLog.append(node);
    }
    node.className = `chat-message ${entry.role}`;
    node.dataset.status = entry.status;
    node.querySelector('.speaker').textContent = entry.role === 'user' ? '你' : entry.role === 'assistant' ? 'CityLens' : '会话提示';
    node.querySelector('.bubble').textContent = entry.text;
    const result = messageResults.get(entry.id);
    let attachments = node.querySelector('.message-results');
    if (result && entry.role === 'assistant') {
      if (!attachments) { attachments = document.createElement('div'); attachments.className = 'message-results'; node.append(attachments); }
      const run = result.recommendation_run_id || result.run_id;
      const active = run === currentRunId;
      if (attachments.dataset.run !== result.run_id || attachments.dataset.active !== String(active)) {
        renderMessageResults(result, attachments, active);
        attachments.dataset.run = result.run_id || '';
        attachments.dataset.active = String(active);
      }
    } else if (attachments) attachments.remove();
  }
  document.querySelector('#conversation-empty').hidden = conversation.entries.length > 0;
  document.querySelector('#conversation-state').textContent = sessionId ? '正在继续同一计划' : '新的对话';
  if (nearBottom) chatLog.scrollTop = chatLog.scrollHeight;
}
function responseSummary(data) {
  const parts = [data.answer];
  if (data.confirmed_constraints?.length) parts.push(`当前条件：${data.confirmed_constraints.join(' · ')}`);
  if (data.shops?.length) parts.push(`推荐店铺：${data.shops.map(shop => shop.name).join('、')}`);
  if (data.business_choices?.length) parts.push(data.business_choices.map(choice => `${choice.index}. ${choice.shop_name} · ${choice.title} · ¥${(choice.pay_cents / 100).toFixed(2)}`).join('\n'));
  if (data.action_draft) parts.push('已生成操作草稿，请在确认窗口核对。');
  return parts.filter(Boolean).join('\n\n');
}
function finishChat(id, text, status = 'done', result = null) {
  if (!id) return;
  if (result) messageResults.set(id, result);
  conversation.update(id, text, status);
  if (pendingChatId === id && status !== 'pending') pendingChatId = null;
  renderConversation();
}
function switchConversation() {
  messageResults.clear();
  pendingChatId = null;
  conversation.switchTo(conversationKey());
  renderConversation();
}

async function cancelActiveRequest() {
  const id = activeRequestId;
  if (!id) return;
  activeRequestId = null;
  finishChat(pendingChatId, '任务已取消，可以继续调整或重新发送。', 'cancelled');
  try { await fetch(`/api/requests/${encodeURIComponent(id)}`, { method: 'DELETE' }); } catch (_) {}
  pending?.abort();
}

async function loadMemory() {
  const generation = authGeneration;
  try {
    const response = await fetch('/api/memory');
    if (!response.ok) return;
    const data = await response.json();
    if (!authenticated || generation !== authGeneration) return;
    document.querySelector('#forget').hidden = !data.enabled;
    const p = data.preferences;
    const parts = p ? [p.category && `类别 ${p.category}`, p.area && `商圈 ${p.area}`,
      p.max_price && `人均上限 ¥${p.max_price}`, p.min_score != null && `最低评分 ${p.min_score}`,
      p.wants_coupon && '关注优惠券', p.wants_notes && '查看探店笔记'].filter(Boolean) : [];
    memorySummary.textContent = data.enabled
      ? `已记住：${parts.join('、') || '暂无具体条件'}。可在新计划中使用；明确输入的新条件优先。`
      : '尚未启用长期偏好。点击“记住本次偏好”后，才会在新计划中使用。';
  } catch (_) { if (generation === authGeneration) memorySummary.textContent = '暂时无法读取长期偏好。'; }
}

let historyLoadNumber = 0;
async function restoreConversation(id) {
  const generation = authGeneration, selection = requestNumber;
  send.disabled = true;
  try {
    const response = await fetch(`/api/sessions/${encodeURIComponent(id)}/messages`);
    const data = await response.json();
    if (!authenticated || generation !== authGeneration || sessionId !== id || selection !== requestNumber) return;
    if (!response.ok) {
      if (response.status === 404) {
        sessionId = null;
        try { sessionStorage.removeItem(sessionKey); } catch (_) {}
        switchConversation();
      }
      throw new Error(data.detail || '历史消息暂时无法读取');
    }
    messageResults.clear();
    for (const message of data.messages || []) if (message.result) messageResults.set(message.id, message.result);
    conversation.replace(data.messages);
    if (!data.messages.length) conversation.add('system', '这条历史计划创建于消息保存功能上线前，原始消息无法还原。可以继续调整已保存的条件。');
    renderConversation();
    if (data.last_task_response) renderResponse(data.last_task_response, false);
    if (data.last_response) renderResponse(data.last_response, false);
    else answer.textContent = '已恢复会话，可以继续发送消息。';
    renderConversation();
    chatLog.scrollTop = chatLog.scrollHeight;
  } catch (error) {
    if (generation === authGeneration && selection === requestNumber) answer.textContent = error.message;
  } finally {
    if (generation === authGeneration && selection === requestNumber) send.disabled = !authenticated;
  }
}

async function selectConversation(id) {
  const selection = ++requestNumber, generation = authGeneration;
  await cancelActiveRequest();
  if (!authenticated || generation !== authGeneration || selection !== requestNumber) return;
  sessionId = id;
  currentRunId = null; lastPlan = null;
  document.querySelector('#remember').disabled = true;
  actionRequestNumber += 1;
  if (actionDialog.open) actionDialog.close();
  try { if (sessionKey) sessionStorage.setItem(sessionKey, id); } catch (_) {}
  switchConversation();
  results.replaceChildren(); plans.replaceChildren(); constraints.replaceChildren(); relaxations.replaceChildren();
  document.querySelector('#personal-orders').replaceChildren();
  comparison.hidden = true; decisions.hidden = true; resetTrace();
  answer.textContent = '正在恢复会话…';
  for (const node of historyList.querySelectorAll('[data-session]')) {
    node.classList.toggle('selected', node.dataset.session === id);
    node.querySelector('.history-select').setAttribute('aria-current', node.dataset.session === id ? 'true' : 'false');
  }
  await restoreConversation(id);
  input.focus();
}

async function loadHistory() {
  const generation = authGeneration, load = ++historyLoadNumber;
  try {
    const response = await fetch('/api/sessions');
    if (!response.ok) throw new Error('历史会话读取失败');
    const data = await response.json();
    if (!authenticated || generation !== authGeneration || load !== historyLoadNumber) return;
    historyList.replaceChildren();
    if (!data.sessions?.length) {
      const item = document.createElement('li'); item.className = 'history-empty';
      item.textContent = '还没有会话，开始聊聊吧。'; historyList.append(item);
    }
    let previousGroup = null;
    for (const session of data.sessions || []) {
      const date = new Date(session.updated_at * 1000);
      const today = new Date(); today.setHours(0, 0, 0, 0);
      const group = date >= today ? '今天' : Date.now() - date < 7 * 86400000 ? '7 天内' : '更早';
      if (group !== previousGroup) {
        const label = document.createElement('li'); label.className = 'history-group'; label.textContent = group;
        historyList.append(label); previousGroup = group;
      }
      const item = document.createElement('li'); item.dataset.session = session.session_id;
      item.classList.toggle('selected', session.session_id === sessionId);
      const button = document.createElement('button'); button.type = 'button'; button.className = 'history-select';
      button.setAttribute('aria-current', session.session_id === sessionId ? 'true' : 'false');
      const title = document.createElement('span'); title.textContent = session.title || session.constraints.join('、') || '历史计划';
      const time = document.createElement('small'); time.textContent = date.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' });
      button.append(title, time); button.title = title.textContent;
      button.onclick = () => selectConversation(session.session_id);
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'history-delete';
      remove.textContent = '×'; remove.setAttribute('aria-label', `删除会话：${title.textContent}`);
      remove.onclick = async () => {
        if (!confirm('删除这条会话及其聊天消息？运行审计记录会保留。')) return;
        const active = sessionId === session.session_id;
        if (active) await document.querySelector('#clear').onclick();
        if (!authenticated || generation !== authGeneration) return;
        const response = await fetch(`/api/sessions/${encodeURIComponent(session.session_id)}`, { method: 'DELETE' });
        if (!authenticated || generation !== authGeneration) return;
        if (!response.ok) { answer.textContent = '删除失败，请重试。'; return; }
        loadHistory();
      };
      item.append(button, remove); historyList.append(item);
    }
    await loadTasks();
  } catch (_) {
    if (generation === authGeneration && load === historyLoadNumber) {
      const item = document.createElement('li'); item.textContent = '历史暂时不可用，请稍后重试。'; historyList.replaceChildren(item);
    }
  }
}

async function loadTasks() {
  const generation = authGeneration;
  try {
    const response = await fetch('/api/tasks');
    if (!response.ok) return;
    const data = await response.json();
    if (!authenticated || generation !== authGeneration) return;
    taskHistory.replaceChildren();
    for (const task of data.tasks || []) {
      const item = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = task.phase === 'completed' ? '查看方案' : '恢复只读计划';
      button.disabled = task.phase === 'resumed';
      button.onclick = async () => {
        button.disabled = true;
        try {
          const result = await fetch(`/api/tasks/${encodeURIComponent(task.run_id)}/resume`, { method: 'POST' });
          const value = await result.json();
          if (!authenticated || generation !== authGeneration) return;
          if (!result.ok) throw new Error(value.detail || '恢复失败');
          sessionId = value.session_id;
          if (sessionKey) { try { sessionStorage.setItem(sessionKey, sessionId); } catch (_) {} }
          switchConversation();
          conversation.add('assistant', responseSummary(value)); renderConversation();
          renderResponse(value);
          loadHistory();
        } catch (error) { answer.textContent = error.message; }
        finally { button.disabled = false; }
      };
      item.append(button, ` · ${task.phase} · ${task.run_id.slice(0, 8)}`);
      taskHistory.append(item);
    }
  } catch (_) { /* Existing history remains available. */ }
}

function showDraft(data) {
  selectedActionId = data.action_id;
  selectedActionFree = data.voucher?.pay_cents === 0;
  document.querySelector('#action-title').textContent = data.operation;
  document.querySelector('#action-details').textContent = data.order_id ? `订单号：${data.order_id}` :
    `${data.shop?.name || ''}\n${data.voucher?.title || ''}\n支付金额：${money(data.voucher?.pay_cents)}\n${data.voucher?.rules || ''}` +
    (data.voucher?.stock == null ? '' : `\n预览库存：${data.voucher.stock}`) +
    (data.voucher?.begin_time ? `\n秒杀时段：${data.voucher.begin_time} 至 ${data.voucher.end_time}` : '');
  document.querySelector('#action-notice').textContent = data.notice;
  actionStatus.textContent = `请核对后确认；有效期约 ${data.expires_in_seconds} 秒。`;
  const button = document.querySelector('#action-confirm');
  button.hidden = false;
  button.disabled = false;
  button.textContent = `确认${data.operation}`;
  document.querySelector('#action-check').hidden = true;
  document.querySelector('#action-order-cancel').hidden = true;
  if (!actionDialog.open) actionDialog.showModal();
}

async function loadActionHistory() {
  const generation = authGeneration;
  try {
    const response = await fetch('/api/actions/recent');
    if (!response.ok) return;
    const data = await response.json();
    if (!authenticated || generation !== authGeneration) return;
    actionHistory.replaceChildren();
    if (!data.actions?.length) {
      const item = document.createElement('li');
      item.textContent = '暂无券操作。';
      actionHistory.append(item);
    }
    for (const action of data.actions || []) {
      const item = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = `查看券 ${action.voucher_id} · ${action.state}`;
      button.onclick = async () => {
        if (action.state === 'prepared') {
          const response = await fetch(`/api/actions/${encodeURIComponent(action.action_id)}/preview`);
          const draft = await response.json();
          if (!authenticated || generation !== authGeneration) return;
          if (!response.ok) { answer.textContent = draft.detail || '草稿不可恢复'; return; }
          showDraft(draft);
          return;
        }
        selectedActionId = action.action_id;
        selectedActionFree = action.free;
        document.querySelector('#action-title').textContent = '券操作状态';
        document.querySelector('#action-details').textContent = '';
        document.querySelector('#action-notice').textContent = '';
        document.querySelector('#action-confirm').hidden = true;
        document.querySelector('#action-check').hidden = false;
        document.querySelector('#action-order-cancel').hidden = true;
        actionDialog.showModal();
        document.querySelector('#action-check').click();
      };
      item.append(button);
      actionHistory.append(item);
    }
  } catch (_) { if (generation === authGeneration) actionHistory.replaceChildren(); }
}

document.querySelector('#remember').onclick = async () => {
  if (!authenticated || !lastPlan) return;
  const p = lastPlan;
  const preferences = { category: p.stops?.length ? null : p.category, area: p.area,
    max_price: p.stops?.length ? null : p.max_price, min_score: p.min_score,
    wants_coupon: Boolean(p.wants_coupon), wants_notes: Boolean(p.wants_notes) };
  try {
    const response = await fetch('/api/memory', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ consent: true, preferences }) });
    if (!response.ok) throw new Error('保存失败');
    await loadMemory();
  } catch (_) { memorySummary.textContent = '长期偏好保存失败，请稍后重试。'; }
};

document.querySelector('#forget').onclick = async () => {
  try {
    const response = await fetch('/api/memory', { method: 'DELETE' });
    if (!response.ok) throw new Error('删除失败');
    await loadMemory();
  } catch (_) { memorySummary.textContent = '删除失败，请稍后重试。'; }
};

function money(cents) { return `¥${(Number(cents) / 100).toFixed(2)}`; }

async function openVoucherAction(shop, coupon, runId = currentRunId) {
  if (!runId || runId !== currentRunId || !authenticated) return;
  const thisRequest = ++actionRequestNumber;
  const button = document.querySelector('#action-confirm');
  selectedActionId = null;
  button.hidden = false;
  button.disabled = true;
  document.querySelector('#action-check').hidden = true;
  document.querySelector('#action-order-cancel').hidden = true;
  document.querySelector('#action-title').textContent = '核对优惠券信息';
  document.querySelector('#action-details').textContent = '';
  document.querySelector('#action-notice').textContent = '';
  actionStatus.textContent = '正在从点评重新读取券信息…';
  actionDialog.showModal();
  try {
    const response = await fetch('/api/actions/vouchers/prepare', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ run_id: runId, shop_id: shop.id, voucher_id: coupon.id }),
    });
    const data = await response.json();
    if (thisRequest !== actionRequestNumber || !authenticated || !actionDialog.open) return;
    if (!response.ok) throw new Error(data.detail || '无法准备操作');
    selectedActionId = data.action_id;
    selectedActionFree = data.voucher.pay_cents === 0;
    document.querySelector('#action-title').textContent = data.operation;
    document.querySelector('#action-details').textContent =
      `${data.shop.name}\n${data.voucher.title || '优惠券'}\n支付金额：${money(data.voucher.pay_cents)} · 券面金额：${money(data.voucher.face_cents)}` +
      (data.voucher.stock == null ? '' : `\n当前显示库存：${data.voucher.stock}`) +
      (data.voucher.rules ? `\n使用规则：${data.voucher.rules}` : '');
    document.querySelector('#action-notice').textContent = data.notice;
    actionStatus.textContent = `预览有效期约 ${data.expires_in_seconds} 秒。请核对后再确认。`;
    button.textContent = `确认${data.operation}`;
    button.disabled = false;
  } catch (error) { if (thisRequest === actionRequestNumber) actionStatus.textContent = error.message; }
}

function showOrderState(data) {
  const status = data.order_status;
  document.querySelector('#action-order-cancel').hidden = !(status === 1 && data.cancellable);
  if (status === 2) actionStatus.textContent = `${selectedActionFree ? '免费券已领取' : '订单已支付'}。订单号：${data.order_id}`;
  else if (status === 1) actionStatus.textContent = `订单已创建，待支付。订单号：${data.order_id}。当前项目尚未接入支付。`;
  else if (status === 4) actionStatus.textContent = `待支付订单已取消。订单号：${data.order_id}。`;
  else if (data.state === 'submitted') actionStatus.textContent = `已提交，订单可能仍在异步处理中。订单号：${data.order_id}。`;
  else if (data.state === 'uncertain') actionStatus.textContent = '提交结果尚未确认，请查询状态，勿重复提交。';
  else actionStatus.textContent = `当前状态：${data.state}`;
  if (status === 1) {
    actionStatus.textContent = `订单已创建，待支付。订单号：${data.order_id}。`;
    const pay = document.createElement('button'); pay.type = 'button'; pay.textContent = '模拟支付';
    pay.onclick = () => { actionDialog.close(); openMockPayment(data.order_id); }; actionStatus.append(pay);
  }
}

document.querySelector('#action-confirm').onclick = async () => {
  if (!selectedActionId) return;
  const button = document.querySelector('#action-confirm');
  button.disabled = true;
  actionStatus.textContent = '正在提交点评订单…';
  try {
    const response = await fetch(`/api/actions/${encodeURIComponent(selectedActionId)}/confirm`, { method: 'POST' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || '提交失败');
    button.hidden = true;
    document.querySelector('#action-check').hidden = false;
    showOrderState(data);
    loadActionHistory();
  } catch (error) {
    button.hidden = true;
    document.querySelector('#action-check').hidden = false;
    actionStatus.textContent = `${error.message}。可查询订单状态。`;
  }
};

document.querySelector('#action-check').onclick = async () => {
  if (!selectedActionId) return;
  try {
    const response = await fetch(`/api/actions/${encodeURIComponent(selectedActionId)}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || '查询失败');
    showOrderState(data);
    loadActionHistory();
  } catch (error) { actionStatus.textContent = error.message; }
};

document.querySelector('#action-order-cancel').onclick = async () => {
  if (!selectedActionId || !window.confirm('确认取消这个待支付普通券订单？此操作会更新点评订单状态。')) return;
  const button = document.querySelector('#action-order-cancel');
  button.disabled = true;
  try {
    const response = await fetch(`/api/actions/${encodeURIComponent(selectedActionId)}/cancel-order`, { method: 'POST' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || '取消失败');
    showOrderState(data);
    loadActionHistory();
  } catch (error) { actionStatus.textContent = error.message; }
  finally { button.disabled = false; }
};

document.querySelector('#action-cancel').onclick = () => { actionRequestNumber += 1; actionDialog.close(); };

function setUser(user) {
  authGeneration += 1;
  authenticated = Boolean(user);
  if (!authenticated) {
    actionRequestNumber += 1;
    currentRunId = null;
    lastPlan = null;
    document.querySelector('#remember').disabled = true;
    document.querySelector('#forget').hidden = true;
    memorySummary.textContent = '登录后可选择是否记住本次找店偏好。';
    const hint = document.createElement('li'); hint.className = 'history-empty';
    hint.textContent = '连接点评账号后查看历史会话。';
    historyList.replaceChildren(hint);
    actionHistory.replaceChildren();
    taskHistory.replaceChildren();
    document.querySelector('#personal-orders').replaceChildren();
    decisions.hidden = true;
    decisions.querySelector('ul').replaceChildren();
    if (actionDialog.open) actionDialog.close();
    document.querySelector('#payment-dialog')?.close();
    document.querySelector('#account-orders')?.replaceChildren();
    results.replaceChildren();
    plans.replaceChildren();
    comparison.hidden = true;
    comparison.querySelector('tbody').replaceChildren();
    constraints.replaceChildren();
    relaxations.replaceChildren();
    resetTrace();
    input.value = '';
    coordinates = null;
    document.querySelector('#location').classList.remove('active');
    document.querySelector('#location').textContent = '使用我的位置';
    answer.textContent = '';
  }
  document.body.dataset.auth = authenticated ? 'member' : 'guest';
  document.querySelector('#chat-workspace').hidden = !authenticated;
  document.querySelector('#login-gate').hidden = authenticated;
  document.querySelector('#login-gate-status').textContent = '登录后即可找店、继续对话，并查看自己的券和订单。';
  document.querySelector('#open-login').disabled = false;
  accountToggle.disabled = false;
  const nickname = authenticated ? String(user.nickName || `用户 ${user.id}`).replace(/1[3-9]\d{9}/g, value => value.slice(0, 3) + '****' + value.slice(-4)) : '登录 CityLens';
  document.querySelector('#account-name').textContent = nickname;
  document.querySelector('#account-avatar').textContent = authenticated ? nickname.slice(0, 1).toUpperCase() : 'C';
  document.querySelector('#account-subtitle').textContent = authenticated ? '账号与偏好' : '登录后开始聊天';
  accountToggle.setAttribute('aria-label', authenticated ? `管理账号：${nickname}` : '登录 CityLens');
  document.querySelector('.account-options').hidden = !authenticated;
  document.querySelector('#clear').disabled = !authenticated;
  input.disabled = !authenticated;
  document.querySelectorAll('.samples button').forEach(button => { button.disabled = !authenticated; });
  authFields.hidden = authenticated;
  logoutButton.hidden = !authenticated;
  authTitle.textContent = authenticated ? nickname : '登录 CityLens';
  authStatus.textContent = authenticated ? '计划和运行记录仅当前点评账号可访问。' : '连接后即可使用 CityLens，计划和运行记录会绑定到该账号。';
  sessionKey = authenticated ? `citylens-session:${user.id}` : null;
  sessionId = null;
  if (sessionKey) {
    try { sessionId = sessionStorage.getItem(sessionKey); } catch (_) { /* 存储可能不可用。 */ }
  }
  switchConversation();
  send.disabled = !authenticated;
  if (authenticated) { loadMemory(); loadHistory(); loadActionHistory(); if (sessionId) restoreConversation(sessionId); }
}

async function bootAuth() {
  if (location.protocol === 'file:') {
    setUser(null);
    document.querySelector('#login-gate-status').textContent = '请通过 http://127.0.0.1:8090 打开 CityLens。';
    authStatus.textContent = '请通过 http://127.0.0.1:8090 打开 CityLens。';
    answer.textContent = '当前 file:// 页面无法连接 API，请使用 http://127.0.0.1:8090。';
    return;
  }
  try {
    const response = await fetch('/api/auth/me');
    if (response.ok) {
      setUser((await response.json()).user);
      return;
    }
    setUser(null);
  } catch (_) {
    setUser(null);
    authStatus.textContent = '无法连接 CityLens 服务，请检查 8090 端口。';
    document.querySelector('#login-gate-status').textContent = authStatus.textContent;
  }
}

document.querySelector('#send-code').onclick = async () => {
  const phone = document.querySelector('#phone').value.trim();
  if (!/^1[3-9]\d{9}$/.test(phone)) { authStatus.textContent = '请输入 11 位手机号。'; return; }
  const button = document.querySelector('#send-code');
  button.disabled = true;
  try {
    const response = await fetch('/api/auth/code', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ phone }) });
    const data = await response.json();
    authStatus.textContent = response.ok ? '验证码已发送。本地演示环境请在点评 Java 日志中查看。' : (data.detail || '发送失败');
  } catch (_) { authStatus.textContent = '发送失败，请检查点评服务。'; }
  finally { button.disabled = false; }
};

document.querySelector('#login').onclick = async () => {
  const phone = document.querySelector('#phone').value.trim();
  const code = document.querySelector('#code').value.trim();
  if (!/^1[3-9]\d{9}$/.test(phone) || !/^\d{6}$/.test(code)) { authStatus.textContent = '请输入手机号和 6 位验证码。'; return; }
  const button = document.querySelector('#login');
  button.disabled = true;
  try {
    const response = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ phone, code }) });
    const data = await response.json();
    if (!response.ok) { authStatus.textContent = data.detail || '连接失败'; return; }
    setUser(data.user);
    accountDialog.close();
    document.querySelector('#code').value = '';
    answer.textContent = '点评账号已连接，可以开始制定计划。';
  } catch (_) { authStatus.textContent = '连接失败，请检查点评服务。'; }
  finally { button.disabled = false; }
};

logoutButton.onclick = async () => {
  await cancelActiveRequest();
  requestNumber += 1;
  let revoked = false;
  try {
    const response = await fetch('/api/auth/logout', { method: 'POST' });
    if (response.ok) revoked = Boolean((await response.json()).token_revoked);
  } catch (_) { /* 本地界面仍清空；令牌是否撤销待重新连接核验。 */ }
  setUser(null);
  accountDialog.close();
  document.querySelector('#login-gate-status').textContent = '已退出账号，请重新登录后继续聊天。';
  answer.textContent = revoked ? '已退出账号，点评令牌已撤销。' : '已清除 CityLens 登录状态；点评令牌撤销未确认。';
};

document.querySelectorAll('[data-query]').forEach(button => button.onclick = () => {
  input.value = button.dataset.query;
  input.focus();
});

document.querySelector('#location').onclick = () => {
  if (!authenticated) { openAccount(); return; }
  if (!navigator.geolocation) {
    answer.textContent = '当前浏览器不支持获取位置，请输入商圈。';
    return;
  }
  navigator.geolocation.getCurrentPosition(position => {
    coordinates = { longitude: position.coords.longitude, latitude: position.coords.latitude };
    document.querySelector('#location').classList.add('active');
    document.querySelector('#location').textContent = '位置已获取';
    answer.textContent = '位置已获取，可输入“附近的美食”。';
  }, () => { answer.textContent = '未能获取位置，请输入商圈。'; });
};

document.querySelector('#clear').onclick = async () => {
  if (!authenticated) { openAccount(); return; }
  const selection = ++requestNumber, generation = authGeneration;
  await cancelActiveRequest();
  if (selection !== requestNumber || generation !== authGeneration) return;
  if (!sessionId) conversation.clear();
  sessionId = null;
  switchConversation();
  currentRunId = null;
  lastPlan = null;
  document.querySelector('#remember').disabled = true;
  actionRequestNumber += 1;
  if (actionDialog.open) actionDialog.close();
  try { if (sessionKey) sessionStorage.removeItem(sessionKey); } catch (_) {}
  coordinates = null;
  document.querySelector('#location').classList.remove('active');
    document.querySelector('#location').textContent = '使用我的位置';
  input.value = '';
  answer.textContent = '已开启新对话，旧会话保留在左侧历史记录中。';
  loadHistory();
  results.replaceChildren();
  plans.replaceChildren();
  comparison.hidden = true;
  comparison.querySelector('tbody').replaceChildren();
  constraints.replaceChildren();
  relaxations.replaceChildren();
  decisions.hidden = true;
  decisions.querySelector('ul').replaceChildren();
  resetTrace();
  send.disabled = !authenticated;
  input.focus();
};

function renderShop(shop, index, compact = false, runId = currentRunId, interactive = true) {
  const box = document.createElement(compact ? 'div' : 'article');
  box.className = compact ? 'stop' : 'card';
  if (compact) {
    const label = document.createElement('div');
    label.className = 'label';
    label.textContent = `第 ${index + 1} 站`;
    box.append(label);
  } else if (shop.image) {
    const img = document.createElement('img');
    img.src = shop.image;
    img.alt = shop.name;
    box.append(img);
  }
  const inner = document.createElement('div');
  inner.className = compact ? '' : 'inner';
  const link = document.createElement('a');
  link.href = shop.url;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  link.textContent = shop.name;
  if (compact) inner.append(link);
  else {
    const title = document.createElement('h3');
    title.append(link);
    inner.append(title);
  }
  for (const line of [shop.reason, shop.area || shop.address,
    shop.open_hours ? `登记营业时间：${shop.open_hours}` : null]) {
    if (line) {
      const p = document.createElement('p');
      p.textContent = line;
      inner.append(p);
    }
  }
  if (shop.coupons?.length) {
    const p = document.createElement('p');
    p.textContent = `可查看优惠：${shop.coupons.map(coupon => coupon.title || '优惠券').join('、')}`;
    inner.append(p);
    if (currentRunId) {
      const actions = document.createElement('div');
      actions.className = 'coupon-actions';
      for (const coupon of shop.coupons) {
        if (!coupon.id) continue;
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = Number(coupon.payValue) === 0 ? '查看领取条件' : '查看下单条件';
        button.disabled = !interactive;
        button.title = interactive ? "核对当前优惠券信息" : "历史推荐，请重新查询后操作";
        button.onclick = () => openVoucherAction(shop, coupon, runId);
        actions.append(button);
      }
      inner.append(actions);
    }
  }
  const notes = (shop.evidence || []).filter(item => item.field === 'blog');
  if (notes.length) {
    const details = document.createElement('details');
    const summary = document.createElement('summary');
    summary.textContent = `查看 ${notes.length} 篇探店笔记来源`;
    details.append(summary);
    for (const note of notes) {
      const p = document.createElement('p');
      const source = document.createElement('a');
      source.href = note.url;
      source.target = '_blank';
      source.rel = 'noopener noreferrer';
      source.textContent = note.title || '探店笔记';
      p.append(source);
      if (note.value) p.append(`：${note.value}`);
      details.append(p);
    }
    inner.append(details);
  }
  box.append(inner);
  return box;
}

function renderMessageResults(data, container, active) {
  container.replaceChildren();
  const hasResults = data.shops?.length || data.itineraries?.length || data.confirmed_constraints?.length || data.personal_orders?.length || data.business_choices?.length || data.relaxations?.length;
  container.hidden = !hasResults;
  if (!hasResults) return;
  const runId = data.recommendation_run_id || data.run_id;
  const chips = document.createElement('div'); chips.className = 'constraints';
  for (const value of [...(data.confirmed_constraints || []), ...(data.memory_applied || []).map(value => `长期偏好：${value}`)]) {
    const chip = document.createElement('span'); chip.textContent = value; chips.append(chip);
  }
  container.append(chips);
  if (data.itineraries?.length) {
    for (const [index, itinerary] of data.itineraries.entries()) {
      const card = document.createElement('article'); card.className = 'plan-card';
      const head = document.createElement('div'); head.className = 'plan-head';
      const title = document.createElement('h3'); title.textContent = `方案 ${index + 1}`;
      const price = document.createElement('span'); price.className = 'plan-price'; price.textContent = `预计 ¥${itinerary.estimated_total}`;
      head.append(title, price); card.append(head);
      const stops = document.createElement('div'); stops.className = 'stops';
      itinerary.stops.forEach((shop, index) => stops.append(renderShop(shop, index, true, runId, active)));
      const note = document.createElement('p'); note.className = 'plan-note'; note.textContent = itinerary.reason;
      card.append(stops, note); container.append(card);
    }
    if (data.itineraries.length > 1) {
      const comparisonBox = document.createElement('details'); const summary = document.createElement('summary'); summary.textContent = '比较这些方案';
      const table = document.createElement('table'); const head = document.createElement('tr');
      for (const label of ['方案','预计总价','各站评分','直线距离','券 / 笔记']) { const cell = document.createElement('th'); cell.scope='col'; cell.textContent=label; head.append(cell); }
      const thead = document.createElement('thead'); thead.append(head); table.append(thead);
      const tbody = document.createElement('tbody');
      data.itineraries.forEach((item,index) => {
        const row = document.createElement('tr');
        const values = [`方案 ${index+1}`, `¥${item.estimated_total}`, item.stops.map(s=>s.score ?? '暂无').join(' / '), item.straight_distance_m == null ? '暂无坐标' : `${item.straight_distance_m} 米`, `${item.stops.reduce((n,s)=>n+(s.coupons?.length || 0),0)} 张券 / ${item.stops.reduce((n,s)=>n+(s.evidence || []).filter(e=>e.field==='blog').length,0)} 篇笔记`];
        for (const value of values) { const cell=document.createElement('td'); cell.textContent=value; row.append(cell); }
        tbody.append(row);
      });
      table.append(tbody); comparisonBox.append(summary,table); container.append(comparisonBox);
    }
  } else if (data.shops?.length) {
    const grid = document.createElement('div'); grid.className = 'grid';
    data.shops.forEach((shop, index) => grid.append(renderShop(shop, index, false, runId, active)));
    container.append(grid);
  }
  for (const items of [data.personal_orders, data.business_choices]) {
    if (!items?.length) continue;
    const list = document.createElement('ul');
    const states = {1:'待支付',2:'已领取 / 已支付',3:'已核销',4:'已取消',5:'退款中',6:'已退款'};
    for (const item of items) {
      const line = document.createElement('li');
      line.textContent = item.order_id ? `${item.title || '优惠券'} · ${states[item.status] || item.status} · 订单号 ${item.order_id}`
        : `${item.index}. ${item.shop_name} · ${item.title || '优惠券'} · ${money(item.pay_cents)}`;
      if (item.order_id && item.status === 1) {
        const pay = document.createElement('button'); pay.type = 'button'; pay.textContent = '模拟支付';
        pay.onclick = () => openMockPayment(item.order_id); line.append(pay);
      }
      list.append(line);
    }
    container.append(list);
  }
  if (data.candidate_decisions?.length) {
    const details = document.createElement('details'); const summary = document.createElement('summary'); summary.textContent = '查看候选筛选与排序原因';
    const list = document.createElement('ul');
    for (const item of data.candidate_decisions) {
      const line = document.createElement('li'); line.textContent = `${item.name}：${(item.reasons || []).join('；')}`; list.append(line);
    }
    details.append(summary, list); container.append(details);
  }
  if (data.relaxations?.length) {
    const options = document.createElement('div'); options.className = 'relaxations';
    for (const option of data.relaxations) {
      const button = document.createElement('button'); button.type = 'button'; button.textContent = option.label; button.title = option.reason;
      button.disabled = !active;
      button.onclick = () => { if (!authenticated || runId !== currentRunId) return; input.value = option.message; document.querySelector('#form').requestSubmit(); };
      options.append(button);
    }
    container.append(options);
  }
  const hint = document.createElement('p'); hint.className = 'result-context';
  hint.textContent = active ? '本轮查询结果 · 券操作会先重新核对并请你确认。' : '历史查询快照 · 可打开详情，优惠券操作请重新查询。';
  container.append(hint);
  if (data.shops?.length || data.itineraries?.length) {
    const notice = document.createElement('p'); notice.className = 'notice';
    notice.textContent = '价格按店铺人均估算，距离为直线距离。营业时间、优惠条件及实际金额以店铺为准。';
    container.append(notice);
  }
}

function renderResponse(data, openDraft = true) {
  if (data.response_kind === 'conversation') {
    renderTrace(data);
    answer.textContent = data.answer;
    return;
  }
  if (data.recommendation_run_id) currentRunId = data.recommendation_run_id;
  else if (data.plan || data.shops?.length && !data.action_draft) currentRunId = data.run_id || null;
  lastPlan = data.status === 'ok' ? data.plan : null;
  document.querySelector('#remember').disabled = !authenticated || !lastPlan;
  answer.textContent = data.answer;
  if (data.plan_changes?.length) {
    const labels = { stops: '活动顺序或单站预算', area: '商圈', max_price: '人均上限', min_price: '相对价格下限', min_score: '最低评分',
      party_size: '人数', total_budget: '总预算', visit_time: '到店时间', wants_coupon: '优惠需求',
      wants_notes: '探店需求', sort: '排序', category: '类别', keyword: '店铺关键词' };
    answer.textContent += ` 本轮已调整：${[...new Set(data.plan_changes.map(change => labels[change.field] || change.field))].join('、')}。`;
  }
  document.querySelector('#personal-orders').replaceChildren();
  const states = { 1: '待支付', 2: '已领取 / 已支付', 3: '已核销', 4: '已取消', 5: '退款中', 6: '已退款' };
  for (const order of data.personal_orders || []) {
    const line = document.createElement('li');
    line.textContent = `${order.title || '优惠券'} · ${states[order.status] || order.status} · 订单号 ${order.order_id}`;
    document.querySelector('#personal-orders').append(line);
  }
  if (data.business_choices?.length) {
    answer.textContent += '\n' + data.business_choices.map(choice =>
      `${choice.index}. ${choice.shop_name} · ${choice.title || '优惠券'} · ${money(choice.pay_cents)} · 券编号 ${choice.voucher_id}`).join('\n');
  }
  if (data.business_state === 'abandoned' && actionDialog.open) actionDialog.close();
  if (data.action_draft && openDraft) { showDraft(data.action_draft); loadActionHistory(); }
  relaxations.replaceChildren();
  renderDecisions(data.candidate_decisions, data.candidate_summary);
  for (const option of data.relaxations || []) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = option.label;
    button.title = option.reason;
    button.onclick = () => {
      input.value = option.message;
      document.querySelector('#form').requestSubmit();
    };
    relaxations.append(button);
  }
  constraints.replaceChildren();
  for (const value of data.confirmed_constraints || []) {
    const chip = document.createElement('span');
    chip.textContent = value;
    constraints.append(chip);
  }
  for (const value of data.memory_applied || []) {
    const chip = document.createElement('span');
    chip.textContent = `长期偏好：${value}`;
    constraints.append(chip);
  }
  plans.replaceChildren();
  results.replaceChildren();
  const comparisonBody = comparison.querySelector('tbody');
  comparisonBody.replaceChildren();
  comparison.hidden = !(data.itineraries?.length > 1);
  if (data.itineraries?.length) {
    data.itineraries.forEach((itinerary, index) => {
      const card = document.createElement('article');
      card.className = 'plan-card';
      const head = document.createElement('div');
      head.className = 'plan-head';
      const title = document.createElement('h2');
      title.textContent = `方案 ${index + 1}`;
      const price = document.createElement('div');
      price.className = 'plan-price';
      price.textContent = `预计 ¥${itinerary.estimated_total}`;
      head.append(title, price);
      card.append(head);
      const stops = document.createElement('div');
      stops.className = 'stops';
      itinerary.stops.forEach((shop, stopIndex) => stops.append(renderShop(shop, stopIndex, true)));
      card.append(stops);
      const note = document.createElement('p');
      note.className = 'plan-note';
      note.textContent = itinerary.reason;
      card.append(note);
      plans.append(card);
      if (data.itineraries.length > 1) {
        const row = document.createElement('tr');
        const scores = itinerary.stops.map(shop => shop.score == null ? '暂无' : shop.score.toFixed(1)).join(' / ');
        const couponCount = itinerary.stops.reduce((count, shop) => count + (shop.coupons?.length || 0), 0);
        const noteCount = itinerary.stops.reduce((count, shop) => count + (shop.evidence || []).filter(item => item.field === 'blog').length, 0);
        const faceSaving = itinerary.stops.reduce((sum, shop) => sum + (shop.coupon_face_saving_yuan || 0), 0);
        const couponLabel = data.plan?.wants_coupon
          ? `${couponCount} 张券${faceSaving ? ` · 券面差额 ¥${faceSaving.toFixed(2)}` : ''}` : '券未查询';
        const noteLabel = data.plan?.wants_notes && index === 0 ? `${noteCount} 篇笔记` : '笔记未查询';
        for (const value of [`方案 ${index + 1}${index === 0 ? ' · 当前首选' : ''}`,
          `¥${itinerary.estimated_total}`, scores,
          itinerary.straight_distance_m == null ? '暂无坐标' : `${itinerary.straight_distance_m} 米`,
          `${couponLabel} / ${noteLabel}`]) {
          const cell = document.createElement('td');
          cell.textContent = value;
          row.append(cell);
        }
        comparisonBody.append(row);
      }
    });
  } else {
    data.shops?.forEach((shop, index) => results.append(renderShop(shop, index)));
  }
  renderTrace(data);
}

function renderDecisions(items, summary = []) {
  decisions.querySelector('ul').replaceChildren();
  decisions.hidden = !items?.length;
  document.querySelector('#candidate-summary').textContent = summary?.length
    ? summary.map(item => `第 ${item.stop} 站：读取 ${item.searched} 家，复核通过 ${item.verified} 家，进入展示方案 ${item.displayed} 家`).join('；') + '。以下仅展示部分逐店原因。'
    : '';
  for (const item of items || []) {
    const line = document.createElement('li');
    const status = item.outcome === 'selected' ? '入选' : item.outcome === 'alternative' ? '备选' : '未入选';
    line.textContent = `${item.name}（${status}）：${item.reasons.join('；')}`;
    decisions.querySelector('ul').append(line);
  }
}

function renderTrace(data) {
  trace.hidden = !data.events?.length;
  traceToggle.disabled = trace.hidden || !authenticated;
  const id = data.run_id || '';
  trace.querySelector('#run-id').textContent = id;
  const link = trace.querySelector('#run-link');
  link.href = id ? `/api/runs/${encodeURIComponent(id)}` : '#';
  link.hidden = !id;
  const replayLink = trace.querySelector('#replay-link');
  replayLink.href = id ? `/?run_id=${encodeURIComponent(id)}` : '#';
  replayLink.hidden = !id;
  const traceList = trace.querySelector('ol');
  traceList.replaceChildren();
  const labels = {
    agent_turn_start: '推理轮开始', agent_turn_end: '推理轮结束', agent_loop_end: 'Agent 循环结束',
    conversation_handoff: '调用业务工作流', workflow_observation: '读取工作流结果',
    conversation_reply: '模型生成回复', requirements_missing: '检查缺失条件',
    run_start: '任务开始', planner_rules: '规则解析', planner_model: '模型规划',
    memory_applied: '应用长期偏好',
    planner_fallback: '规划回退', plan: '确认计划', model_start: '模型请求',
    model_end: '模型响应', model_response: '行动选择', agent_decision: '执行行动',
    agent_rejected: '拒绝越权行动', tool_start: '调用工具', tool_end: '工具结果',
    candidate_validation: '硬条件校验', itinerary_validation: '路线时段校验',
    evidence_validation: '笔记来源校验', model_fallback: '行动回退',
    inspection_failed: '详情读取失败', run_end: '任务结束', session_conflict: '会话版本冲突',
  };
  for (const item of data.events || []) {
    const line = document.createElement('li');
    const detail = [item.tool, item.shop_id == null ? null : `店铺 ${item.shop_id}`,
      item.phase, item.mode, item.reason, item.error,
      item.before == null ? null : `${item.before} → ${item.after}`,
      item.ok == null ? null : (item.ok ? '成功' : '失败')].filter(Boolean).join(' · ');
    line.textContent = `${item.elapsed_ms} ms · ${labels[item.event] || item.event}${detail ? ` · ${detail}` : ''}`;
    traceList.append(line);
  }
}

const replayId = new URLSearchParams(location.search).get('run_id');
bootAuth().then(async () => {
  if (!replayId || !authenticated) return;
  try {
    const response = await fetch(`/api/runs/${encodeURIComponent(replayId)}`);
    if (!response.ok) throw new Error('运行记录已过期');
    const record = await response.json();
    if (record.response) {
      renderResponse(record.response);
      return;
    }
    answer.textContent = `运行记录 ${record.run_id} · 状态 ${record.status} · ${record.shop_ids?.length || 0} 家店铺`;
    renderDecisions(record.candidate_decisions, record.candidate_summary);
    renderTrace(record);
    openTrace();
  } catch (_) { answer.textContent = '运行记录不存在、已过期，或不属于当前点评账号。'; }
});

document.querySelector('#form').onsubmit = async event => {
  event.preventDefault();
  if (!authenticated) { openAccount(); return; }
  const message = input.value.trim();
  if (!message) return;
  const currentRequest = ++requestNumber;
  await cancelActiveRequest();
  if (currentRequest !== requestNumber) return;
  relaxations.replaceChildren();
  pending = new AbortController();
  const requestId = crypto.randomUUID();
  activeRequestId = requestId;
  conversation.bind(conversationKey());
  conversation.add('user', message);
  const chatId = conversation.add('assistant', '正在理解你的需求…', 'pending');
  pendingChatId = chatId;
  input.value = '';
  renderConversation();
  send.disabled = true;
  answer.textContent = 'Agent 正在理解你的消息…';
  try {
    const response = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, session_id: sessionId, request_id: requestId, context_run_id: currentRunId, ...(coordinates || {}) }),
      signal: pending.signal,
    });
    if (response.status === 401) { setUser(null); throw new Error('auth'); }
    if (response.status === 404 && sessionId) { sessionId = null; if (sessionKey) sessionStorage.removeItem(sessionKey); throw new Error('session'); }
    if (!response.ok) throw new Error('请求失败');
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let data = null;
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary;
      while ((boundary = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        if (!frame.startsWith('data: ')) continue;
        const item = JSON.parse(frame.slice(6));
        if (currentRequest !== requestNumber) return;
        if (item.type === 'event') {
          if (item.data.event === 'session_started') {
            sessionId = item.data.session_id;
            try { if (sessionKey) sessionStorage.setItem(sessionKey, sessionId); } catch (_) {}
            conversation.bind(conversationKey());
            renderConversation();
          }
          const labels = { conversation_handoff: '正在进入业务查询与校验', planner_rules: '正在理解需求', planner_model: '正在使用模型规划',
            tool_start: item.data.tool === 'run_workflow' ? '正在执行受控业务工作流' : '正在查询点评数据', candidate_validation: '正在复核候选店铺',
            itinerary_validation: '正在校验两站预算和时段', evidence_validation: '正在核对探店证据' };
          if (labels[item.data.event]) {
            answer.textContent = `${labels[item.data.event]}…`;
            finishChat(chatId, answer.textContent, 'pending');
          }
        } else if (item.type === 'result') data = item.data;
        else if (item.type === 'cancelled') throw new Error('cancelled');
        else if (item.type === 'error') {
          if (item.status === 401) { setUser(null); throw new Error('auth'); }
          throw new Error(item.detail || '请求失败');
        }
      }
    }
    if (!data) throw new Error('请求中断');
    if (currentRequest !== requestNumber) return;
    if (data.session_id) sessionId = data.session_id;
    if (sessionId) {
      try { if (sessionKey) sessionStorage.setItem(sessionKey, sessionId); } catch (_) {}
    }
    conversation.bind(conversationKey());
    renderResponse(data);
    finishChat(chatId, data.answer, 'done', data);
    loadHistory();
  } catch (error) {
    if (error.name !== 'AbortError' && currentRequest === requestNumber) {
      answer.textContent = error.message === 'auth' ? '点评登录已失效，请重新连接账号。' :
        error.message === 'session' ? '上一轮会话已失效，请重新提交需求。' :
        error.message === 'cancelled' ? '任务已取消。' : `运行失败：${error.message}`;
      finishChat(chatId, answer.textContent, 'error');
    }
  } finally {
    if (activeRequestId === requestId) activeRequestId = null;
    if (currentRequest === requestNumber) send.disabled = !authenticated;
  }
};

document.querySelector('#stop-request').onclick = async () => {
  if (!activeRequestId) return;
  requestNumber += 1;
  await cancelActiveRequest();
  answer.textContent = '任务已取消。';
  send.disabled = !authenticated;
};


let paymentPreview = null;
const paymentDialog = document.querySelector('#payment-dialog');
document.querySelector('#payment-close').onclick = () => paymentDialog.close();
async function openMockPayment(orderId) {
  const generation = authGeneration;
  paymentPreview = null;
  const button = document.querySelector('#payment-confirm'); button.disabled = true; button.hidden = false;
  document.querySelector('#payment-status').textContent = '正在核对当前账号订单…';
  document.querySelector('#payment-details').textContent = '';
  if (!paymentDialog.open) paymentDialog.showModal();
  try {
    const response = await fetch(`/api/my/orders/${encodeURIComponent(orderId)}`); const data = await response.json();
    if (generation !== authGeneration || !authenticated || !paymentDialog.open) return;
    if (!response.ok) throw new Error(data.detail || '订单查询失败');
    if (data.status !== 1 || !(data.pay_cents > 0)) throw new Error('订单不是待支付付费券');
    paymentPreview = {orderId, amount: data.pay_cents};
    document.querySelector('#payment-details').textContent = `订单号：${orderId}\n模拟支付金额：${money(data.pay_cents)}`;
    document.querySelector('#payment-status').textContent = '请核对后确认。'; button.disabled = false;
  } catch (error) { document.querySelector('#payment-status').textContent = error.message; }
}
document.querySelector('#payment-confirm').onclick = async () => {
  if (!paymentPreview) return;
  const button = document.querySelector('#payment-confirm'); button.disabled = true;
  try {
    const {orderId, amount} = paymentPreview;
    const response = await fetch(`/api/my/orders/${encodeURIComponent(orderId)}/pay/mock`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_pay_cents:amount,confirmed:true})});
    const data = await response.json(); if (!response.ok) throw new Error(data.detail || '支付结果未确认，请重新查询订单');
    document.querySelector('#payment-status').textContent = data.notice + ' 券已进入待核销状态，可查询“我的券”。';
    button.hidden = true; paymentPreview = null; loadActionHistory();
  } catch (error) { document.querySelector('#payment-status').textContent = error.message + '；请重新查询状态后再操作。'; }
};


document.querySelector('#load-my-orders').onclick = async () => {
  const generation = authGeneration;
  const list = document.querySelector('#account-orders'); list.textContent = '正在查询…';
  try {
    const response = await fetch('/api/my/orders'); const data = await response.json();
    if (generation !== authGeneration || !authenticated) return;
    if (!response.ok) throw new Error(data.detail || '订单查询失败');
    list.replaceChildren();
    const states = {1:'待支付',2:'待核销',3:'已核销',4:'已取消'};
    for (const item of data.items || []) {
      const line = document.createElement('li'); line.textContent = `${item.title || '优惠券'} · ${states[item.status] || item.status} · ${money(item.pay_cents)} · 订单号 ${item.order_id}`;
      if (item.status === 1) {
        const button = document.createElement('button'); button.type = 'button'; button.textContent = '模拟支付';
        button.onclick = () => { accountDialog.close(); openMockPayment(item.order_id); }; line.append(button);
      }
      list.append(line);
    }
    if (!data.items?.length) list.textContent = '暂无订单。';
  } catch (error) { list.textContent = error.message; }
};
