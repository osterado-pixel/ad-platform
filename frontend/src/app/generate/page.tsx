import type { Metadata } from "next";

import { GeneratePanel } from "@/components/GeneratePanel";

export const metadata: Metadata = {
  title: "AI-копирайтер",
  description: "Три варианта рекламного объявления по описанию товара — за несколько секунд.",
};

export default function GeneratePage() {
  return (
    <section className="section">
      <div className="container" style={{ maxWidth: 760 }}>
        <h1>AI-копирайтер</h1>
        <p className="muted">
          Опишите товар или услугу — ИИ предложит три варианта заголовка и текста объявления.
        </p>
        <GeneratePanel />
      </div>
    </section>
  );
}
