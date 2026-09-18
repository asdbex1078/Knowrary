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
