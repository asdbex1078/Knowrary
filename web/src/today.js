/**
 * "今天是几号" —— 按**本地**日期。
 *
 * 这个文件存在的唯一理由是 `new Date().toISOString().slice(0, 10)` 不能用：
 * `toISOString()` 先转成 UTC，于是在 UTC+8 的本地 00:00~08:00 之间，它说的是**昨天**。
 * 踩到的地方一个比一个疼：
 *
 * - `learned` 写进 md frontmatter，是日历"这天建了几个"的唯一来源——错了就永久错了；
 * - `placedAt` 决定草稿放了多久；
 * - 热力图每一格的 key 全部往前错一天，**今天那格顶着昨天的 key 去查，于是永远查不到**
 *   （表现是标题写着"1 天有动静"、格子一个都不亮）。
 *
 * 后端早就为同一件事做过本地化（`core/calendar.py` 的 `_day_of`），这边是漏掉的那一半。
 */

/** `Date` → `YYYY-MM-DD`，按本地时区。 */
export function localISO(date) {
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  return `${date.getFullYear()}-${m}-${d}`
}

/** 本地的今天，`YYYY-MM-DD`。 */
export const todayISO = () => localISO(new Date())
