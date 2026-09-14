import { responseErrorMessage } from "../api";
export const assistantBase = `${import.meta.env.VITE_API_BASE_URL ?? "/api/v1"}/assistant`;
export async function assistantApi<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await fetch(assistantBase + path, {
    method,
    headers:
      body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok)
    throw new Error(await responseErrorMessage(response, "问知意"));
  return response.json() as Promise<T>;
}
