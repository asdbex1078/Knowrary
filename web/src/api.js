// 与本地服务的三个接口对话。index 只读，layout 读写，Markdown 永不从这里改。

async function request(url, options) {
  const res = await fetch(url, options)
  if (!res.ok) {
    const err = new Error(`${options?.method || 'GET'} ${url} → ${res.status}`)
    err.status = res.status
    try { err.body = await res.json() } catch { err.body = null }
    throw err
  }
  return res.json()
}

export const fetchIndex = () => request('/api/index')
export const fetchLayout = () => request('/api/layout')
export const fetchHealth = () => request('/api/health')

export const fetchNode = (id) => request(`/api/node/${encodeURIComponent(id)}`)

/** Markdown 写回：dry_run=true 只预览，false 才落盘（服务端写前自动备份）。 */
export const postChanges = (body) => request('/api/changes', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const patchLayout = (body) => request('/api/layout', {
  method: 'PATCH',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

// —— 阶段 4：Inbox / 放置 / 欠账清单 / 复习 ——
export const fetchInbox = () => request('/api/inbox')
export const fetchDigest = () => request('/api/digest')
export const fetchDue = () => request('/api/review/due')

/** 把 Inbox 节点放上画布。不给 group/at 就由服务端按邻居投票找位置。 */
export const postPlace = (body) => request('/api/place', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

/** 记一次复习：只写 review-log.json，不碰 md，也不碰 layout。 */
export const postReview = (id) => request(`/api/review/${encodeURIComponent(id)}`, { method: 'POST' })

// —— 阶段 5：vault 的 assets/ 图片 ——
export const fetchAssets = () => request('/api/assets')

/** 原始 body 直传（服务端不依赖 multipart）。name 会成为 assets/ 下的文件名。 */
export const uploadAsset = (name, file) => request(
  `/api/asset/${encodeURIComponent(name)}`,
  { method: 'POST', headers: { 'Content-Type': file.type || 'application/octet-stream' }, body: file },
)
