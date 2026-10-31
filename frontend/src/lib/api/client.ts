import type { ApiErrorBody } from "@/types/api";

export class ApiError extends Error {
  constructor(message: string, readonly status: number) { super(message); this.name = "ApiError"; }
}

function streamNetworkError(error: unknown, responseStarted: boolean): ApiError {
  if (error instanceof ApiError) return error;
  if (error instanceof DOMException && error.name === "AbortError") {
    return new ApiError("The chat request was cancelled.", 499);
  }
  if (!responseStarted) {
    return new ApiError("The BankEase API connection ended before a response arrived. Check the backend logs, then retry.", 503);
  }
  return new ApiError("The response stream was interrupted before it was saved. Retry this message.", 502);
}

function csrfCookie() {
  const entry = document.cookie.split(";").map((part) => part.trim()).find((part) => part.startsWith("bankease_csrf="));
  return entry ? decodeURIComponent(entry.slice("bankease_csrf=".length)) : "";
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    let csrf = csrfCookie();
    if (!csrf) {
      await fetch("/api/v1/auth/csrf", { credentials: "same-origin", cache: "no-store" });
      csrf = csrfCookie();
    }
    if (csrf) headers.set("X-CSRF-Token", csrf);
  }
  const response = await fetch(path, { ...init, method, headers, credentials: "same-origin", cache: "no-store" });
  if (response.status === 204) return undefined as T;
  const type = response.headers.get("content-type") ?? "";
  if (!response.ok) {
    let body: ApiErrorBody = {};
    try { body = type.includes("application/json") ? await response.json() as ApiErrorBody : { detail: await response.text() }; } catch { /* Use a status fallback. */ }
    const detail = body.detail;
    const text = typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((item) => item.msg).filter(Boolean).join(" ") : `Request failed (${response.status}).`;
    throw new ApiError(text, response.status);
  }
  if (type.includes("application/pdf") || type.includes("application/zip") || type.includes("application/octet-stream")) return await response.blob() as T;
  return await response.json() as T;
}

export async function apiStream<T>(path: string, body: unknown,
                                   onDelta: (text: string) => void,
                                   signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    let csrf = csrfCookie();
    if (!csrf) {
      await fetch("/api/v1/auth/csrf", { credentials: "same-origin", cache: "no-store", signal });
      csrf = csrfCookie();
    }
    response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(csrf ? { "X-CSRF-Token": csrf } : {}) },
      body: JSON.stringify(body), credentials: "same-origin", cache: "no-store", signal,
    });
  } catch (error) {
    throw streamNetworkError(error, false);
  }
  if (!response.ok) {
    let payload: ApiErrorBody = {};
    try { payload = await response.json() as ApiErrorBody; } catch { /* Use status fallback. */ }
    const detail = payload.detail;
    const message = typeof detail === "string" ? detail : Array.isArray(detail)
      ? detail.map((item) => item.msg).filter(Boolean).join(" ")
      : `Request failed (${response.status}).`;
    throw new ApiError(message, response.status);
  }
  if (!response.body) throw new ApiError("This browser cannot read streamed responses.", 501);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completed: T | undefined;
  const consume = (frame: string) => {
    let event = "message";
    const data: string[] = [];
    for (const line of frame.split(/\r?\n/)) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trim());
    }
    if (!data.length) return;
    let payload: { text?: string; message?: string } & T;
    try { payload = JSON.parse(data.join("\n")) as typeof payload; }
    catch { throw new ApiError("The assistant returned an invalid stream event.", 502); }
    if (event === "delta" && typeof payload.text === "string") onDelta(payload.text);
    if (event === "error") throw new ApiError(payload.message || "The assistant stream failed.", 503);
    if (event === "done") completed = payload as T;
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let boundary = buffer.search(/\r?\n\r?\n/);
      while (boundary >= 0) {
        const frame = buffer.slice(0, boundary);
        const separator = buffer.slice(boundary).match(/^\r?\n\r?\n/)?.[0] ?? "\n\n";
        buffer = buffer.slice(boundary + separator.length);
        consume(frame);
        boundary = buffer.search(/\r?\n\r?\n/);
      }
      if (done) break;
    }
    if (buffer.trim()) consume(buffer);
  } catch (error) {
    throw streamNetworkError(error, true);
  } finally {
    if (completed === undefined) await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
  if (completed === undefined) throw new ApiError("The assistant stream ended before a response was saved.", 502);
  return completed;
}

export const jsonRequest = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  ...(body === undefined ? {} : { body: JSON.stringify(body) }),
});
