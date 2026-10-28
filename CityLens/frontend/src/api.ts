export async function api(path: string, method = 'GET', body?: unknown): Promise<any> {
  if (import.meta.env.MODE === 'demo') return (await import('./demo.mjs')).demoApi(path, method, body)
  const r = await fetch(path, { method, credentials: 'same-origin', headers: body === undefined ? {} : { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
  const data = await r.json()
  if (!r.ok) { if (r.status === 401) window.dispatchEvent(new Event('auth-expired')); throw new Error(typeof data.detail === 'string' ? data.detail : '请求失败，请检查输入或稍后重试') }
  return data
}
export function imageUrl(value: unknown): string {
  const path = String(value || '').split(',')[0]
  const origin = import.meta.env.VITE_DIANPING_ASSET_ORIGIN || `${location.protocol}//${location.hostname}:8080`
  if (/^\/imgs\/[\w/.-]+$/.test(path)) return `${origin}${path}`
  if (/^\/(blogs|types|icons)\/[\w/.-]+$/.test(path)) return `${origin}/imgs${path}`
  if (/^https?:\/\//.test(path)) return path
  return ''
}
export const money = (cents: unknown) => (Number(cents || 0) / 100).toFixed(2)
export const statusName = (status: unknown) => ({1:'待支付',2:'已支付 / 可使用',3:'已核销',4:'已取消'}[Number(status)] || '处理中')
