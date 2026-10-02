// 抽成模型谱系的计划（2026-09-30）：几篇原文各抽一份计划，这里并成一份给人勾、再组装成写入请求。
//
// 纯函数、不碰网络——单元测试直接喂数据。服务端每篇给的是
// `{ path, title, source, families: [{ name, target, digest, models: [{ name, status, fields, conflicts, suspicious }] }] }`。

/**
 * 几份计划按目标文件并起来：同一个家族文件只出现一次；同一个型号在两篇里都抽到了，
 * 先出现的格子为准、后面的只补先前没有的格子（和写入时「只补不覆盖」同一个口径）。
 * 每个型号带上 `from`（出自哪几篇）和 `pick`（默认勾上：有东西可写才勾）。
 */
export function mergeProposals(proposals) {
  const byTarget = new Map()
  for (const p of proposals) {
    for (const fam of p.families || []) {
      const have = byTarget.get(fam.target) || { ...fam, models: [], sources: [] }
      for (const m of fam.models || []) mergeModel(have.models, m, p)
      // 出处跟着家族走：这个家族是从哪几篇里抽出来的
      for (const s of fam.sources || [p.source]) if (s && !have.sources.includes(s)) have.sources.push(s)
      byTarget.set(fam.target, have)
    }
  }
  return [...byTarget.values()]
}

function mergeModel(list, m, proposal) {
  const key = m.name.toLowerCase()
  const have = list.find((x) => x.name.toLowerCase() === key)
  // 可疑记录带上出自哪篇：review 要对着**那一篇**的原文判
  const suspects = (m.suspicious || []).map((x) => ({ ...x, path: proposal.path }))
  if (!have) {
    list.push({ ...m, fields: { ...m.fields }, suspicious: suspects, from: [proposal.path],
                pick: Object.keys(m.fields || {}).length > 0 })
    return
  }
  const taken = Object.keys(m.fields || {}).filter((k) => !(k in have.fields))
  for (const k of taken) have.fields[k] = m.fields[k]
  have.conflicts = [...(have.conflicts || []), ...(m.conflicts || [])]
  // 这一格留下的是先前那篇的值时，后面这篇对**它自己那个值**的可疑就不相干了
  have.suspicious = [...(have.suspicious || []), ...suspects.filter((x) => taken.includes(x.key))]
  if (!have.from.includes(proposal.path)) have.from.push(proposal.path)
  have.pick = have.pick || Object.keys(have.fields).length > 0
}

/** 要问 review 的格子，按出自哪篇分组（review 要对着那一篇的原文判）：`{ path: [{ model, key, value }] }`。 */
export function suspectsByArticle(families) {
  const out = {}
  for (const fam of families) {
    for (const m of fam.models) {
      for (const s of m.suspicious || []) {
        const path = s.path || m.from[0]
        ;(out[path] ||= []).push({ model: m.name, key: s.key, value: m.fields[s.key] ?? s.value })
      }
    }
  }
  return out
}

/** 写入请求：只带勾上的型号、有格子要写的；一个型号都没剩的家族整个不带。 */
export function applyBody(families, proposals) {
  const picked = families
    .map((fam) => ({
      name: fam.name, target: fam.target, exists: fam.exists, digest: fam.digest,
      vendor: fam.vendor || '', tagline: fam.tagline || '', open: fam.open ?? null, sources: fam.sources || [],
      models: fam.models.filter((m) => m.pick && Object.keys(m.fields).length)
        .map((m) => ({ name: m.name, status: m.status, fields: m.fields, conflicts: [], suspicious: [] })),
    }))
    .filter((fam) => fam.models.length)
  return { families: picked }
}

export function pickedCount(families) {
  return families.reduce((n, fam) => n + fam.models.filter((m) => m.pick && Object.keys(m.fields).length).length, 0)
}

/** 预览里一个型号的一行摘要：发布 · 参数 / 激活 · 前几个技术点。 */
export function summaryOf(fields) {
  const size = [fields['参数'], fields['激活'] && `激活 ${fields['激活']}`].filter(Boolean).join(' / ')
  return [fields['发布'], size, fields['上下文'] && `上下文 ${fields['上下文']}`].filter(Boolean).join(' · ')
}
