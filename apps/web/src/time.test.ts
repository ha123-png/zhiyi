import { describe, expect, it } from "vitest";
import { hasTimezoneMarker, parseServerTime, serverDate } from "./time";

describe("parseServerTime", () => {
  it("treats naive server timestamps as UTC (SQLite drops the Z suffix)", () => {
    // 后端存 UTC，但 SQLite 读出后无时区标记；缺 Z 时按 UTC 解析，
    // 否则 UTC+8 环境会被当成本地时间产生 8 小时偏差（历史时间差 8h、计时 480 分钟同根因）。
    const ms = parseServerTime("2026-08-08T02:00:00");
    expect(ms).toBe(Date.parse("2026-08-08T02:00:00Z"));
  });

  it("keeps timestamps that already carry a timezone marker", () => {
    expect(parseServerTime("2026-08-08T02:00:00Z")).toBe(
      Date.parse("2026-08-08T02:00:00Z"),
    );
    expect(parseServerTime("2026-08-08T10:00:00+08:00")).toBe(
      Date.parse("2026-08-08T10:00:00+08:00"),
    );
  });

  it("returns NaN for empty input", () => {
    expect(Number.isNaN(parseServerTime(""))).toBe(true);
    expect(Number.isNaN(parseServerTime("not-a-date"))).toBe(true);
  });
});

describe("hasTimezoneMarker", () => {
  it("distinguishes naive from marked strings", () => {
    expect(hasTimezoneMarker("2026-08-08T02:00:00")).toBe(false);
    expect(hasTimezoneMarker("2026-08-08T02:00:00Z")).toBe(true);
    expect(hasTimezoneMarker("2026-08-08T10:00:00+08:00")).toBe(true);
  });
});

describe("serverDate", () => {
  it("builds a Date from the UTC epoch", () => {
    const d = serverDate("2026-08-08T02:00:00");
    expect(d.getTime()).toBe(Date.parse("2026-08-08T02:00:00Z"));
  });
});
