"use client";

// AI-копирайтер: запуск фоновой генерации и опрос статуса.
//
//   const task = useAdTask();
//   await task.start("Онлайн-курс Python для начинающих", "Студенты 18–25 лет");
//   task.status    — "idle" | "pending" | "processing" | "completed" | "failed" | "timeout"
//   task.variants  — варианты после "completed"; task.error — причина при "failed"
//
// Деньги на время генерации замораживает сервер, после неё — списывает по факту или возвращает.
// cancel() и уход со страницы только прекращают опрос: задача на сервере доработает.
import { useCallback, useEffect, useRef, useState } from "react";

import { api, type AdVariant, type AITask, type AITaskCreated } from "@/services/api";

const POLL_LIMIT_MS = 150_000; // дольше генерация не идёт — дальше задачу закроет очистка зависших
const FIRST_DELAY_MS = 1000;
const MAX_DELAY_MS = 4000;

export type AdTaskStatus = "idle" | "pending" | "processing" | "completed" | "failed" | "timeout";

type State = {
  status: AdTaskStatus;
  taskId: string | null;
  variants: AdVariant[] | null;
  error: string | null;
  heldAmount: number | null;
  elapsed: number;
};

const IDLE: State = { status: "idle", taskId: null, variants: null, error: null, heldAmount: null, elapsed: 0 };

// Пауза, которую прерывает отмена (иначе опрос дождался бы паузы после ухода со страницы)
function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => { clearTimeout(timer); reject(signal.reason); }, { once: true });
  });
}

export function useAdTask() {
  const [state, setState] = useState<State>(IDLE);
  const controllerRef = useRef<AbortController | null>(null);

  // Компонент убран со страницы — опрос прекращается, setState после этого не вызывается
  useEffect(() => () => controllerRef.current?.abort(), []);

  const start = useCallback(async (productDescription: string, targetAudience = "Общая аудитория") => {
    controllerRef.current?.abort(); // новый запуск отменяет опрос предыдущего
    const controller = new AbortController();
    controllerRef.current = controller;
    const { signal } = controller;

    setState({ ...IDLE, status: "pending" });
    try {
      // 402 — мало денег, 422 — текст не прошёл модерацию, 429 — много незавершённых задач,
      // 503 — копирайтер выключен: всё это — failed с причиной от сервера
      const task = await api<AITaskCreated>("/ai/generate-async", {
        method: "POST",
        signal,
        body: JSON.stringify({ product_description: productDescription, target_audience: targetAudience }),
      });
      setState((s) => ({ ...s, taskId: task.task_id, heldAmount: task.held_amount }));

      const started = Date.now();
      let delay = FIRST_DELAY_MS;
      for (;;) {
        await sleep(delay, signal);
        delay = Math.min(delay * 1.5, MAX_DELAY_MS);
        const t = await api<AITask>(`/ai/tasks/${task.task_id}`, { signal });
        const elapsed = Math.round((Date.now() - started) / 1000);
        if (t.status === "completed" && t.result) {
          const variants = t.result.variants;
          setState((s) => ({ ...s, status: "completed", variants, elapsed }));
          return variants;
        }
        if (t.status === "failed") {
          setState((s) => ({ ...s, status: "failed", error: t.error ?? "Генерация не удалась", elapsed }));
          return null;
        }
        if (Date.now() - started > POLL_LIMIT_MS) {
          setState((s) => ({
            ...s, status: "timeout", elapsed,
            error: "Генерация идёт дольше обычного. Если задача не завершится, деньги вернутся автоматически.",
          }));
          return null;
        }
        setState((s) => ({ ...s, status: t.status, elapsed }));
      }
    } catch (err) {
      if (signal.aborted) return null; // отменено — состояние не трогаем
      setState((s) => ({ ...s, status: "failed", error: err instanceof Error ? err.message : String(err) }));
      return null;
    }
  }, []);

  const cancel = useCallback(() => {
    controllerRef.current?.abort();
    setState(IDLE);
  }, []);

  return { ...state, start, cancel, isRunning: state.status === "pending" || state.status === "processing" };
}
