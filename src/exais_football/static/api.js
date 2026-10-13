export class ApiError extends Error {
  constructor(message, status = 0, code = "request_failed") {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function readResponse(response) {
  let data;
  try { data = await response.json(); } catch { data = null; }
  if (!response.ok) {
    const detail = data?.error;
    const validation = Array.isArray(data?.detail) ? data.detail.map(item => item.msg).join("; ") : null;
    throw new ApiError(detail?.message || validation || `The request could not be completed (${response.status}).`, response.status, detail?.code);
  }
  return data;
}

export async function api(path, options = {}) {
  const { body, ...rest } = options;
  const headers = new Headers(rest.headers || {});
  const init = { ...rest, headers, credentials: "same-origin" };
  if (body !== undefined) {
    headers.set("Content-Type", "application/json");
    init.body = JSON.stringify(body);
  }
  try { return await readResponse(await fetch(`/api${path}`, init)); }
  catch (error) {
    if (error instanceof ApiError || error.name === "AbortError") throw error;
    throw new ApiError("Cannot reach your local film room. Check that the application is running.");
  }
}

export function upload(path, form, onProgress) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", `/api${path}`);
    request.responseType = "json";
    request.upload.addEventListener("progress", event => {
      if (event.lengthComputable) onProgress?.(Math.round(event.loaded / event.total * 100));
    });
    request.addEventListener("load", () => {
      const data = request.response;
      if (request.status >= 200 && request.status < 300) resolve(data);
      else reject(new ApiError(data?.error?.message || `Import failed (${request.status}).`, request.status, data?.error?.code));
    });
    request.addEventListener("error", () => reject(new ApiError("Upload interrupted. Check the local application and try this file again.")));
    request.addEventListener("abort", () => reject(new ApiError("Upload canceled.")));
    request.send(form);
  });
}

export function localURL(value) {
  if (!value) return null;
  try {
    const url = new URL(value, window.location.origin);
    return url.origin === window.location.origin && ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch { return null; }
}

export function queryString(values) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== "" && value !== null && value !== undefined) params.set(key, String(value));
  }
  return params.toString();
}
