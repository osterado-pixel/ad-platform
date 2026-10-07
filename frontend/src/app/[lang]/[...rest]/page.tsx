import { notFound } from "next/navigation";

// Неизвестный адрес внутри языка (/en/нет-такой) → «не найдено» на этом языке (not-found.tsx рядом)
export default function CatchAll() {
  notFound();
}
