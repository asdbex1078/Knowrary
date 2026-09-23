/**
 * 统一 diff 按行分类，给「变更卡」和 Inspector 的预览共用。
 *
 * 后端 `curation.diff_of()` 出两种东西：改已有文件给 `difflib.unified_diff` 片段，
 * **新建文件直接给全文**（每行都是新增，加 `+` 前缀按 60 行截断只会把正文后半截藏掉）。
 * 所以这里先认出是哪一种——没有 `@@` 就是新文件全文，整篇按正文色走，
 * 由调用方在头上打「新文件」标记，而不是刷一片绿。
 *
 * 另外，`--- a/x.md` / `+++ b/x.md` 这两行头也以 `-` / `+` 开头，
 * 光看首字符会把文件名染成红绿一整行（Inspector 之前就是这样）。路径在卡片上方已经
 * 用 <b> 显示过了，这两行直接丢掉。
 */
function parse(text) {
  const raw = String(text || '')
  const lines = raw.split('\n')
  const isNew = !lines.some((l) => l.startsWith('@@'))
  if (isNew) return { isNew: true, lines: lines.map((text) => ({ text, cls: '' })) }
  const out = []
  for (const line of lines) {
    if (line.startsWith('--- ') || line.startsWith('+++ ')) continue   // 文件头，路径另有地方显示
    if (line.startsWith('@@')) out.push({ text: line, cls: 'hunk' })
    else if (line.startsWith('+')) out.push({ text: line, cls: 'add' })
    else if (line.startsWith('-')) out.push({ text: line, cls: 'del' })
    else out.push({ text: line, cls: '' })
  }
  return { isNew: false, lines: out }
}

// 模板里一个文件要问两次（打不打「新文件」标记、拿哪些行），而渲染每帧都会重跑。
// diff 文本是不可变的，按原文记一次就够；卡片重算 diff 会换成新字符串，自然错开。
const memo = new Map()

export function parseDiff(text) {
  const key = String(text || '')
  if (!memo.has(key)) {
    if (memo.size > 200) memo.clear()      // 聊久了别无限长
    memo.set(key, parse(key))
  }
  return memo.get(key)
}

/**
 * 「按意见修改」的改前 → 改后：笔记一段就是一行，改了几个字整段红一遍绿一遍，人得自己找不同。
 * 把相邻的删 / 增行两两配对，掐掉公共的头尾，**只把中间真正变了的那几个字标出来**（`hot`）。
 */
export function pairDiff(text) {
  const lines = parseDiff(text).lines.map((l) => ({ ...l, parts: [{ text: l.text, hot: false }] }))
  for (let i = 0; i < lines.length;) {
    if (lines[i].cls !== 'del') { i++; continue }
    let d = i
    while (d < lines.length && lines[d].cls === 'del') d++
    let a = d
    while (a < lines.length && lines[a].cls === 'add') a++
    for (let k = 0; k < Math.min(d - i, a - d); k++) mark(lines[i + k], lines[d + k])
    i = Math.max(a, i + 1)
  }
  return lines
}

function mark(del, add) {
  const x = del.text.slice(1)
  const y = add.text.slice(1)
  let head = 0
  while (head < x.length && head < y.length && x[head] === y[head]) head++
  let tail = 0
  while (tail < x.length - head && tail < y.length - head && x[x.length - 1 - tail] === y[y.length - 1 - tail]) tail++
  for (const [line, s, sign] of [[del, x, '-'], [add, y, '+']]) {
    line.parts = [{ text: sign + s.slice(0, head), hot: false },
                  { text: s.slice(head, s.length - tail), hot: true },
                  { text: s.slice(s.length - tail), hot: false }].filter((p) => p.text)
  }
}
