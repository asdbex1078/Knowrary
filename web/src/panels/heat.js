/**
 * 学习日历热力图的纯逻辑：一天算几分、铺成几列、每格是哪一天。
 *
 * **单独成文件是为了能测。** 它踩过一个只有在 UTC 以东才会犯的错（见 `../today.js`），
 * 而那种错在界面上的样子是"标题说今天有动静、格子却一个都没亮"——
 * 看起来像后端没算出来，其实是前端把每一格都读错了一天。
 */
import { localISO } from '../today.js'


/** 热力等级：建一个节点算 3 分，复习 / 答题各 1 分。**不算模型调用**——
 *  那是花销不是学习量，算进去会让"跟模型聊了一下午"看起来像"学了一整天"。 */
export const weight = (c) => (c ? c.built * 3 + c.reviews + c.answers : 0)

export const level = (c) => {
  const w = weight(c)
  return w === 0 ? 0 : w <= 2 ? 1 : w <= 5 ? 2 : w <= 10 ? 3 : 4
}

/** 按周分列铺：一列一周，周一在上。范围外的格子给 null（渲染成占位的空格）。 */
export function weekColumns(from, to) {
  if (!from || !to) return []
  const end = new Date(`${to}T00:00:00`)
  const cursor = new Date(`${from}T00:00:00`)
  cursor.setDate(cursor.getDate() - ((cursor.getDay() + 6) % 7))   // 回退到周一
  const out = []
  while (cursor <= end) {
    const col = []
    for (let i = 0; i < 7; i += 1) {
      const iso = localISO(cursor)
      col.push(iso >= from && iso <= to ? iso : null)
      cursor.setDate(cursor.getDate() + 1)
    }
    out.push(col)
  }
  return out
}
