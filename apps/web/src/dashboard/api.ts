import { responseErrorMessage } from "../api";
import type { DashboardRange } from "../types";
import type { DashboardOverview, Statistic, StatisticInput, StatisticResult, StatisticTable } from "./types";

const base = `${import.meta.env.VITE_API_BASE_URL ?? "/api/v1"}/stats`;
async function request<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(base + path, {
      method,
      ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
    });
  } catch {
    throw new Error("无法连接知意服务，请检查服务状态后重试。");
  }
  if (!response.ok) throw new Error(await responseErrorMessage(response, "统计"));
  return response.status === 204 ? undefined as T : response.json();
}
export const dashboardApi = {
  overview: (days: DashboardRange) => request<DashboardOverview>(`/overview?days=${days}`),
  cards: () => request<{ items: Statistic[]; limit: number }>("/cards"),
  options: () => request<{ tables: StatisticTable[] }>("/card-options"),
  preview: (input: StatisticInput, days: DashboardRange) => request<StatisticResult>(`/cards/preview?days=${days}`, "POST", input),
  save: (input: StatisticInput, existing?: Statistic, days: DashboardRange = 7) => existing
    ? request<Statistic>(`/cards/${encodeURIComponent(existing.id)}?days=${days}`, "PUT", { ...input, expected_updated_at: existing.updated_at })
    : request<Statistic>(`/cards?days=${days}`, "POST", input),
  remove: (id: string) => request<void>(`/cards/${encodeURIComponent(id)}`, "DELETE"),
  order: (ids: string[]) => request<unknown>("/cards/order", "PUT", { ids }),
  result: (id: string, days: DashboardRange) => request<StatisticResult>(`/cards/${encodeURIComponent(id)}/result?days=${days}`),
};
