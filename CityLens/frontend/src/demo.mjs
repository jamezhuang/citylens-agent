import recording from './demo-data.json' with { type: 'json' }

const clone = value => JSON.parse(JSON.stringify(value))
const sessions = new Map(), runs = new Map(), drafts = new Map(), orders = new Map()
const listeners = new Set()
let sequence = 0, stage = 0, writes = 0
export const demoPrompts = [
  '找人均150以内的火锅，不限商圈',
  '预算改成100以内',
  '两人先吃火锅再唱歌，总预算400元',
  '找人均150以内的火锅，有优惠券吗，不限商圈'
]
export function demoSnapshot() { return { stage, writes, orderCount: orders.size } }
export function subscribeDemo(fn) { listeners.add(fn); return () => listeners.delete(fn) }
function notify() { for (const fn of listeners) fn(demoSnapshot()) }
export function resetDemo() {
  sessions.clear(); runs.clear(); drafts.clear(); orders.clear()
  stage = 0; writes = 0; sequence = 0; notify()
}
function newId(kind) { return `demo-${kind}-${++sequence}` }
const world = recording.world
const couponsFor = id => Object.values(world.vouchers).filter(v => v.shopId === Number(id))
function shop(id) {
  const value = world.shops[String(id)]
  if (!value) throw new Error('此演示中没有这家店铺，请从列表选择。')
  return clone(value)
}
export async function demoChat(payload, signal) {
  if (signal?.aborted) throw new DOMException('已停止', 'AbortError')
  const message = String(payload.message || '')
  const sessionId = sessions.has(payload.session_id) ? payload.session_id : newId('session')
  const session = sessions.get(sessionId) || { session_id: sessionId, title: message, messages: [], last_response: null }
  sessions.set(sessionId, session)
  let key = null
  if (message === demoPrompts[1] && session.last_response) key = 'edit'
  else if (message === demoPrompts[2]) key = 'itinerary'
  else if (message === demoPrompts[3]) key = 'find'
  else if (message === demoPrompts[0] || /火锅/.test(message) && !/券|100|400/.test(message)) key = 'first'
  const result = key ? clone(recording.steps[key].body) : {
    answer: '这是固定案例的交互演示，当前不连接实时模型。请点击上方步骤继续完整案例；你也可以浏览店铺，领取演示券并查看模拟订单。',
    shops: [], itineraries: [], status: 'ok', mode: 'demo-replay', events: []
  }
  result.session_id = sessionId
  result.run_id = newId('run')
  result.recommendation_run_id = result.shops?.length ? result.run_id : null
  result.mode = 'demo-replay'
  result.action_draft = null
  runs.set(result.run_id, { ...clone(result), recording_source: '已验证规则 Agent 的实际执行记录（非本次在线计算）' })
  session.messages.push({ id: newId('message'), role: 'user', text: message },
    { id: newId('message'), role: 'assistant', text: result.answer, result: clone(result), run_id: result.run_id })
  session.last_response = clone(result)
  if (key) stage = Math.max(stage, {first:1,edit:2,itinerary:3,find:4}[key])
  notify()
  return result
}
export async function demoApi(path, method = 'GET', body = {}) {
  const u = new URL(path, 'https://demo.invalid'), p = u.pathname
  if (p === '/api/auth/me' || p === '/api/auth/login') return {user:{id:1,nickName:'演示访客'}}
  if (p === '/api/auth/code' || p === '/api/auth/logout' || p.startsWith('/api/requests/')) return {ok:true}
  if (p === '/api/sessions') return {sessions:[...sessions.values()].map(s => ({session_id:s.session_id,title:s.title})).reverse()}
  if (p.startsWith('/api/sessions/')) {
    const id = p.split('/')[3]
    if (method === 'DELETE') { sessions.delete(id); return {ok:true} }
    const session = sessions.get(id)
    if (!session) return {messages:[],last_response:null}
    return clone(session)
  }
  if (p.startsWith('/api/runs/')) {
    const result = runs.get(p.split('/').pop())
    if (!result) throw new Error('演示记录已重置，请重新开始案例。')
    return clone(result)
  }
  if (p === '/api/tasks') return {tasks:[]}
  if (p === '/api/memory') {
    if (method !== 'GET') throw new Error('公开演示不保存个人偏好，请在本地完整版体验。')
    return {preferences:{}}
  }
  if (p === '/api/catalog/types') return {data:clone(world.types)}
  if (p === '/api/catalog/capabilities') return {can_create_voucher:false}
  if (p === '/api/catalog/shops') {
    let items = Object.values(world.shops)
    const keyword = u.searchParams.get('keyword') || '', type = u.searchParams.get('typeId')
    items = items.filter(s => s.name.toLowerCase().includes(keyword.toLowerCase()) && (!type || String(s.typeId) === type))
    items.sort(u.searchParams.get('sort') === 'price' ? (a,b) => a.avgPrice-b.avgPrice : (a,b) => b.score-a.score)
    const page = Math.max(1,Number(u.searchParams.get('page'))||1)
    return {data:clone(items.slice((page-1)*12,page*12)),total:items.length}
  }
  if (p.startsWith('/api/catalog/shops/')) {
    const s = shop(p.split('/').pop())
    return {shop:s,vouchers:clone(couponsFor(s.id)).map(v => ({...v,orderable:v.id===9001})),blogs:clone(world.blogs.filter(b=>b.shopId===s.id))}
  }
  if (p === '/api/actions/vouchers/prepare' || p === '/api/catalog/prepare') {
    if (Number(body.shop_id) !== 101 || Number(body.voucher_id) !== 9001) throw new Error('完整案例演示使用甲火锅的免费券，请选择“测试免费券”。付费券仅展示。')
    if (body.run_id && !runs.get(body.run_id)?.shops?.some(s=>s.id===101)) throw new Error('请先查询包含甲火锅的商户结果。')
    const draft = clone(recording.steps.prepare.body)
    draft.action_id = newId('action')
    draft.notice = '在线演示：仅生成当前页面的模拟订单，不领取真实权益；刷新页面即重置。'
    drafts.set(draft.action_id,draft)
    stage = Math.max(stage,5); notify()
    return clone(draft)
  }
  if (p.startsWith('/api/actions/')) {
    const id = p.split('/')[3], draft = drafts.get(id)
    if (!draft) throw new Error('草稿不存在或页面已刷新，请重新预览。')
    if (p.endsWith('/confirm') && method === 'POST') {
      if (!draft.order_id) {
        const existing = [...orders.values()].find(o=>o.voucherId===9001)
        draft.order_id = existing?.order_id || newId('order')
        draft.state = 'submitted'
        if (!existing) {
          writes++
          orders.set(draft.order_id,{order_id:draft.order_id,voucherId:9001,title:draft.voucher.title,
            shopName:draft.shop.name,pay_cents:0,payValue:0,status:2,voucher_type:0,
            rules:draft.voucher.rules,created_at:'当前访问 · 浏览器模拟订单'})
        }
      }
      stage = Math.max(stage,6); notify()
    }
    return {action_id:id,state:draft.state,order_id:draft.order_id||null,voucher_id:9001,free:true,kind:'ordinary',order_status:draft.order_id?2:null,cancellable:false}
  }
  if (p === '/api/my/orders' || p === '/api/my/vouchers') return {items:clone([...orders.values()]),total:orders.size}
  if (p.startsWith('/api/my/orders/')) {
    const order = orders.get(p.split('/')[4])
    if (!order) throw new Error('当前演示中没有这个订单。')
    return clone(order)
  }
  if (p === '/api/community/blogs' && method === 'GET') {
    const items = u.searchParams.get('page') && u.searchParams.get('page') !== '1' ? [] : clone(world.blogs).map(b=>({...b,name:'合成演示作者'}))
    return {data:u.searchParams.get('feed')==='follow'?{list:[]}:u.searchParams.get('feed')==='me'?[]:items}
  }
  if (/^\/api\/community\/blogs\/\d+$/.test(p) && method === 'GET') return {data:{...clone(world.blogs[0]),userId:2,name:'合成演示作者',createTime:'演示数据',images:''}}
  if (/^\/api\/community\/authors\/\d+\/follow$/.test(p) && method === 'GET') return {data:false}
  throw new Error('此功能需连接完整业务服务。在线版请使用完整案例、店铺浏览和免费券演示。')
}
