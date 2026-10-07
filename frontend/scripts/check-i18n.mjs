// Полнота переводов: в каждом словаре те же ключи, что в русском, без пустых строк и с теми же
// подстановками ({amount}, {n}…). Запуск: npm run check:i18n (выполняется и в CI)
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const dir = join(dirname(fileURLToPath(import.meta.url)), "..", "src", "i18n", "dictionaries");
const load = (file) => JSON.parse(readFileSync(join(dir, file), "utf8"));
const reference = load("ru.json");
const placeholders = (s) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(",");

function compare(ref, other, path, problems) {
  if (Array.isArray(ref)) {
    if (!Array.isArray(other) || other.length !== ref.length) {
      problems.push(`${path}: ожидался список из ${ref.length} элементов`);
      return;
    }
    ref.forEach((item, i) => compare(item, other[i], `${path}[${i}]`, problems));
  } else if (ref && typeof ref === "object") {
    if (!other || typeof other !== "object" || Array.isArray(other)) {
      problems.push(`${path}: ожидался объект`);
      return;
    }
    for (const key of Object.keys(ref)) {
      if (!(key in other)) problems.push(`${path}.${key}: нет перевода`);
      else compare(ref[key], other[key], `${path}.${key}`, problems);
    }
    for (const key of Object.keys(other)) {
      if (!(key in ref)) problems.push(`${path}.${key}: лишний ключ (нет в ru.json)`);
    }
  } else if (typeof ref === "string") {
    if (typeof other !== "string" || !other.trim()) problems.push(`${path}: пустой перевод`);
    else if (placeholders(ref) !== placeholders(other)) {
      problems.push(`${path}: подстановки {${placeholders(ref)}} ≠ {${placeholders(other)}}`);
    }
  }
}

let failed = false;
for (const file of readdirSync(dir).filter((f) => f.endsWith(".json") && f !== "ru.json")) {
  const problems = [];
  compare(reference, load(file), file.replace(".json", ""), problems);
  if (problems.length) {
    failed = true;
    console.error(`✗ ${file}:\n  ${problems.join("\n  ")}`);
  } else {
    console.log(`✓ ${file}: все переводы на месте`);
  }
}
process.exit(failed ? 1 : 0);
