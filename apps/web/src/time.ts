/**
 * 服务器时间解析统一工具。
 *
 * 后端一律存 UTC（utc_now = datetime.now(timezone.utc)），但 SQLite 读出的
 * datetime 不带时区信息，pydantic 序列化为无时区后缀的 ISO 字符串
 * （如 "2026-08-08T11:47:10"）。浏览器 `new Date(...)` 对无时区字符串按
 * **本地时区**解释，会导致 UTC+8 环境出现 8 小时偏差（历史时间差 8 小时、
 * 计时多出 480 分钟都是这个根因）。
 *
 * 本函数统一规则：字符串若不带时区标记，一律按 UTC 解析（补 Z）。
 */

/** 判断 ISO 字符串是否已带时区标记（Z 或 ±HH:MM）。 */
export function hasTimezoneMarker(iso: string): boolean {
  return /[zZ]$|[+-]\d{2}:\d{2}$/.test(iso.trim());
}

/** 把服务器时间字符串解析为 epoch 毫秒；无时区标记按 UTC 处理。 */
export function parseServerTime(iso: string): number {
  if (!iso) return NaN;
  const value = iso.trim();
  const normalized = hasTimezoneMarker(value) ? value : `${value}Z`;
  const parsed = Date.parse(normalized);
  return Number.isNaN(parsed) ? Date.parse(value) : parsed;
}

/** 把服务器时间字符串解析为 Date 对象（UTC 语义）。 */
export function serverDate(iso: string): Date {
  const ms = parseServerTime(iso);
  return Number.isNaN(ms) ? new Date(0) : new Date(ms);
}
