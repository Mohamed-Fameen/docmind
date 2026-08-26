/**
 * All communication with the FastAPI backend goes through this file — every other
 * component calls these functions rather than calling fetch() directly, so the request
 * shapes only need to match the backend's Pydantic models in exactly one place.
 */

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export interface SourceRef {
  number: number;
  heading_path: string;
  doc_url: string;
  text_snippet: string;
  cited: boolean;
}

export interface QueryResponse {
  answer: string;
  sources: SourceRef[];
  model_used: string;
  classification: string;
  retries: number;
  conversation_id: string;
}

export interface ModelInfo {
  provider: string;
  description: string;
}

export interface ModelsResponse {
  default: string;
  available: Record<string, ModelInfo>;
}

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      // response body wasn't JSON — fall back to statusText
    }
    throw new ApiError(detail, res.status);
  }
  return res.json();
}

export async function register(email: string, password: string): Promise<{ access_token: string }> {
  const res = await fetch(`${API_URL}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  return handleResponse(res);
}

export async function login(email: string, password: string): Promise<{ access_token: string }> {
  // /auth/login expects form-encoded data (OAuth2PasswordRequestForm on the backend),
  // not JSON — see backend/app/main.py's login() docstring for why.
  const form = new URLSearchParams();
  form.set("username", email);
  form.set("password", password);

  const res = await fetch(`${API_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: form.toString(),
  });
  return handleResponse(res);
}

export async function listModels(): Promise<ModelsResponse> {
  const res = await fetch(`${API_URL}/models`);
  return handleResponse(res);
}

export async function sendQuery(
  token: string,
  query: string,
  conversationId: string | null,
  model: string | null
): Promise<QueryResponse> {
  const res = await fetch(`${API_URL}/query`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({
      query,
      conversation_id: conversationId,
      model,
    }),
  });
  return handleResponse(res);
}
