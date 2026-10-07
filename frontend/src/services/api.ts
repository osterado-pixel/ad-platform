// Клиент API платформы. Сайт и кабинет /app — на одном домене (Caddy или rewrites в next.config.ts),
// поэтому адрес относительный, а вход общий: токен кабинет хранит в localStorage под этим ключом
export const API_URL = "/api/v1";
const TOKEN_KEY = "adp_token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null; // приватный режим или запрет хранилища
  }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

// Текст ошибки из ответа API: detail — строка или (при 422) список ошибок полей
function errorText(data: unknown, status: number): string {
  const detail = (data as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((e: { msg?: string }) => e.msg).filter(Boolean).join("; ");
  return `Ошибка сервера (${status})`;
}

export async function api<T>(path: string, options: RequestInit & { token?: string | null } = {}): Promise<T> {
  const { token = getToken(), headers, ...rest } = options;
  const response = await fetch(`${API_URL}${path}`, {
    ...rest,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, errorText(data, response.status));
  return data as T;
}

// --- Типы ответов API (backend/app/schemas.py) ---
export type AdVariant = { title: string; text: string; cta: string };
export type AITaskStatus = "pending" | "processing" | "completed" | "failed";
export type AITask = {
  task_id: string;
  status: AITaskStatus;
  result: { variants: AdVariant[] } | null;
  error: string | null;
  created_at: string;
  updated_at: string;
};
export type AITaskCreated = { task_id: string; status: "pending"; check_status_url: string; held_amount: number };
export type CopywriterStatus = { enabled: boolean; hold_amount: number; max_active_tasks: number };
export type Me = { id: number; email: string; balance: number; held_balance: number; is_admin: boolean };
