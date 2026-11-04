import type { Heatmap, Place, Plan, PlanInput } from "../types";

const BASE = (import.meta.env.VITE_API_URL ?? "").replace(/\/$/, "");

export class ApiError extends Error {
  code: string;
  fields?: Record<string, unknown>;
  status: number;
  constructor(status: number, code: string, message: string, fields?: Record<string, unknown>) {
    super(message);
    this.status = status;
    this.code = code;
    this.fields = fields;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    throw new ApiError(0, "network", "Can't reach the planning server. Check your connection and try again.");
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const e = body?.error;
    throw new ApiError(res.status, e?.code ?? "error", e?.message ?? `Request failed (HTTP ${res.status}).`, e?.fields);
  }
  return body as T;
}

export const api = {
  geocode: (q: string, signal?: AbortSignal) =>
    request<{ results: Place[] }>(`/api/geocode?q=${encodeURIComponent(q)}`, { signal }).then((r) => r.results),
  plan: (input: PlanInput, signal?: AbortSignal) =>
    request<Plan>("/api/plan", { method: "POST", body: JSON.stringify(input), signal }),
  heatmap: (planId: string, signal?: AbortSignal) => request<Heatmap>(`/api/plan/${planId}/heatmap`, { signal }),
  health: () => request<{ status: string; mode: string }>("/api/health"),
};
