// React-хук для AI-копирайтера Ad Platform: запуск фоновой генерации и опрос статуса.
//
// Скопируйте файл в свой проект (React 18+). Адрес фронтенда добавьте в CORS_ORIGINS платформы.
//
//   const task = useAdTask({ apiUrl: "https://ads.example.com/api/v1", token });
//   await task.start("Онлайн-курс Python для начинающих", "Студенты 18–25 лет");
//   task.status    — "idle" | "pending" | "processing" | "completed" | "failed" | "timeout"
//   task.variants  — [{ title, text, cta }] после "completed"
//   task.error     — причина при "failed" (сервер сам пишет, вернулись ли деньги)
//   task.isRunning — идёт ли генерация (для индикатора и блокировки кнопки)
//   task.elapsed   — сколько секунд идёт генерация
//
// Деньги на время генерации замораживаются сервером, после неё списываются по факту или
// возвращаются — клиенту ничего делать не нужно. cancel() и уход со страницы только прекращают
// опрос: задача на сервере доработает, результат будет в GET /ai/tasks.
import { useCallback, useEffect, useRef, useState } from "react";

const POLL_LIMIT_MS = 150000; // дольше генерация не идёт — дальше задачу закроет очистка зависших
const FIRST_DELAY_MS = 1000;
const MAX_DELAY_MS = 4000;

const IDLE = { status: "idle", taskId: null, variants: null, error: null, heldAmount: null, elapsed: 0 };

// Текст ошибки из ответа API: detail — строка или (при 422) список ошибок полей
function errorText(data, status) {
  if (typeof data?.detail === "string") return data.detail;
  if (Array.isArray(data?.detail)) return data.detail.map((e) => e.msg).join("; ");
  return `Ошибка сервера (${status})`;
}

// Пауза, которую прерывает отмена (иначе опрос дождался бы паузы после ухода со страницы)
function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => { clearTimeout(timer); reject(signal.reason); }, { once: true });
  });
}

export function useAdTask({ apiUrl, token }) {
  const [state, setState] = useState(IDLE);
  const controllerRef = useRef(null);

  // Компонент убран со страницы — опрос прекращается, setState после этого не вызывается
  useEffect(() => () => controllerRef.current?.abort(), []);

  const start = useCallback(async (productDescription, targetAudience = "Общая аудитория") => {
    controllerRef.current?.abort(); // новый запуск отменяет опрос предыдущего
    const controller = new AbortController();
    controllerRef.current = controller;
    const { signal } = controller;

    const request = async (path, options = {}) => {
      const response = await fetch(`${apiUrl}${path}`, {
        ...options,
        signal,
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) throw new Error(errorText(data, response.status));
      return data;
    };

    setState({ ...IDLE, status: "pending" });
    try {
      // 402 — мало денег, 422 — текст не прошёл модерацию, 429 — много незавершённых задач,
      // 503 — копирайтер выключен: всё это — failed с причиной от сервера
      const task = await request("/ai/generate-async", {
        method: "POST",
        body: JSON.stringify({ product_description: productDescription, target_audience: targetAudience }),
      });
      setState((s) => ({ ...s, taskId: task.task_id, heldAmount: task.held_amount }));

      const started = Date.now();
      let delay = FIRST_DELAY_MS;
      for (;;) {
        await sleep(delay, signal);
        delay = Math.min(delay * 1.5, MAX_DELAY_MS);
        const t = await request(`/ai/tasks/${task.task_id}`);
        const elapsed = Math.round((Date.now() - started) / 1000);
        if (t.status === "completed") {
          setState((s) => ({ ...s, status: "completed", variants: t.result.variants, elapsed }));
          return t.result.variants;
        }
        if (t.status === "failed") {
          setState((s) => ({ ...s, status: "failed", error: t.error, elapsed }));
          return null;
        }
        if (Date.now() - started > POLL_LIMIT_MS) {
          setState((s) => ({ ...s, status: "timeout", elapsed,
            error: "Генерация идёт дольше обычного. Если задача не завершится, деньги вернутся автоматически." }));
          return null;
        }
        setState((s) => ({ ...s, status: t.status, elapsed }));
      }
    } catch (err) {
      if (signal.aborted) return null; // отменено — состояние не трогаем
      setState((s) => ({ ...s, status: "failed", error: err.message }));
      return null;
    }
  }, [apiUrl, token]);

  const cancel = useCallback(() => {
    controllerRef.current?.abort();
    setState(IDLE);
  }, []);

  return { ...state, start, cancel, isRunning: state.status === "pending" || state.status === "processing" };
}
