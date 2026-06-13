#!/usr/bin/env python3
"""
Benchmark Ollama and OpenRouter models for metadata/places extraction reliability.

Usage:
    # Ollama (default)
    python3 scripts/benchmark_models.py --provider ollama --models gemma4:e2b ...

    # OpenRouter
    python3 scripts/benchmark_models.py --provider openrouter \
        --api-key sk-or-... \
        --models nvidia/nemotron-3-super-120b-a12b:free google/gemma-4-31b-it:free
"""

import argparse
import json
import os
import time
from dataclasses import dataclass, field
from textwrap import indent

# ---------------------------------------------------------------------------
# Sample OCR texts (real scans from the archive)
# ---------------------------------------------------------------------------

OCR_METADATA_TEST = """\
florian.holzerfäkurier.at

TOP Mit Kids auf dem Land

Wald, Wiese, Spielplatz und gutes Essen:
Die besten Kinder-Lokale im Grünen.

1 APFELBAUER
Idylle inmitten der Wiener Hausberge. Groß-
artige Küche mit Produkten aus der Region,
Spielplatz am Bach, gute Kinder-Menüs.

2761 Miesenbach, Ascherstr. 15, @ 02632/8244,
Mi-Sa 11-21, So 11-20, www.apfelbauer.at

2 BONKA

Ein Gasthaus, wie es im Buche steht, nur besser
bekocht. Und auferdem Spielplatz, Pferde und
jede Menge grüner Wienerwald.

3413 Oberkirchbach 61, @ 02242/62 90, Mi-Sa 9-21,
So, Fei 9-16, www.bonka.at

3 NIERSCHER
Das Essen ist hier auf Edelheurigen-Niveau, die
Atmosphäre urig, das Angebot für Kinder prachtvoller.
Ternitz 3, @  02242/62 91 23, www.nierscher.at

Plus & Minus der besten Ausflugslokale Wiens
"""

OCR_PLACES_TEST = """\
Wandern, wenn die Marillenbäume blühen

Wachau. Tipps von Wanderblogger Stephan Schmatz

Die Marillenblüte in der Wachau beginnt früh im Jahr.
Am Montag ist Frühlingsbaeginn. In der Wachau und im Kremstal
sprießen bereits die Knospen auf den Marillenbäumen.

Restaurant Zur Goldenen Traube
Donaustraße 12, 3504 Krems an der Donau
Tel: 02732/85 432, Di-So 11-22, www.goldenetraube.at

Weingut Knoll
Unterloiben 10, 3601 Dürnstein, @02711/8344
Mo-Sa 10-18, www.weingutknoll.at

Heuriger Mauerer
Wachaustraße 5, Spitz an der Donau
täglich 16-24 Uhr, www.heuriger-mauerer.at
"""

# ---------------------------------------------------------------------------
# Prompts (copied from app/worker/ to be self-contained)
# ---------------------------------------------------------------------------

METADATA_PROMPT = """\
Du analysierst den OCR-Text eines eingescannten deutschen Zeitungsartikels.
Extrahiere folgende Metadaten als JSON. Falls ein Feld nicht sicher erkennbar ist, \
setze null — NICHT raten.

Felder:
- newspaper: Name der Zeitung (z.B. "Kurier", "Süddeutsche Zeitung")
- section: Rubrik- oder Beilagenname innerhalb der Zeitung. "Plus/Minus" wenn \
die Wörter "Plus" und "Minus" im OCR-Text vorkommen.
- article_date: Erscheinungsdatum im Format YYYY-MM-DD. null wenn nicht erkennbar.
- page: Seitenangabe als String. null wenn fehlt.
- headline: Hauptschlagzeile des Artikels. Pflichtfeld.
- summary: Zusammenfassung in 2-3 deutschen Sätzen. Pflichtfeld.
- category: Eines von exakt: Politik, Wirtschaft, Kultur, Sport, Ernährung, \
Wissenschaft, Lokales, International, Reise, Plus/Minus, Sonstiges.
- tags: Array mit 3-5 relevanten deutschen Stichwörtern
- locations: Array mit geografischen Ortsnamen
- urls: Array mit Websites und E-Mail-Adressen

Antworte NUR mit validem JSON ohne Markdown-Backticks oder Erklärungen.

OCR-Text:
{ocr_text}
"""

PLACES_PROMPT = """\
Du analysierst den OCR-Text eines eingescannten deutschen Zeitungsartikels.
Extrahiere alle konkreten Orte, Lokale, Hotels, Restaurants die im Text genannt werden.

Gib ein JSON-Array zurück. Jeder Eintrag hat diese Felder (null wenn nicht vorhanden):
- name, description, address, postal_code, city, country, phone, hours, url, rating

rating: "+" positiv, "-" negativ, "+/-" gemischt, null = kein Urteil

Wenn keine Einträge vorhanden, gib [] zurück.
Antworte NUR mit validem JSON ohne Markdown-Backticks.

OCR-Text:
{ocr_text}
"""

VALID_CATEGORIES = {
    "Politik", "Wirtschaft", "Kultur", "Sport", "Ernährung",
    "Wissenschaft", "Lokales", "International", "Reise", "Plus/Minus", "Sonstiges",
}

METADATA_REQUIRED = {"headline", "summary", "category", "tags", "locations", "urls"}
PLACES_FIELDS = {"name", "description", "address", "postal_code", "city",
                 "country", "phone", "hours", "url", "rating"}


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

@dataclass
class TestResult:
    model: str
    task: str
    elapsed_s: float
    raw: str = ""
    parsed: object = None
    error: str = ""
    scores: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.error and self.parsed is not None

    def summary_line(self) -> str:
        status = "OK" if self.ok else "FAIL"
        score_str = "  ".join(f"{k}={v}" for k, v in self.scores.items())
        return f"  [{status}] {self.task:<10}  {self.elapsed_s:5.1f}s  {score_str}"


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return raw


def _parse(raw: str) -> object | None:
    try:
        return json.loads(_strip_fences(raw))
    except json.JSONDecodeError:
        try:
            from json_repair import repair_json
            return json.loads(repair_json(_strip_fences(raw)))
        except Exception:
            return None


def _score_metadata(data: object) -> dict:
    if not isinstance(data, dict):
        return {"valid_json": 0, "required_fields": 0, "valid_category": 0}
    present = sum(1 for k in METADATA_REQUIRED if data.get(k) not in (None, "", []))
    category_ok = int(data.get("category") in VALID_CATEGORIES)
    return {
        "valid_json": 1,
        "required_fields": f"{present}/{len(METADATA_REQUIRED)}",
        "valid_category": category_ok,
        "headline_len": len(str(data.get("headline", ""))),
    }


def _score_places(data: object) -> dict:
    if not isinstance(data, list):
        # might be wrapped in a dict
        if isinstance(data, dict):
            lists = [v for v in data.values() if isinstance(v, list)]
            if lists:
                data = lists[0]
            else:
                return {"valid_json": 0, "places_found": 0}
        else:
            return {"valid_json": 0, "places_found": 0}
    names = [p.get("name") for p in data if isinstance(p, dict) and p.get("name")]
    filled = sum(
        sum(1 for f in PLACES_FIELDS if p.get(f)) / len(PLACES_FIELDS)
        for p in data if isinstance(p, dict)
    )
    avg_fill = round(filled / len(data), 2) if data else 0.0
    return {
        "valid_json": 1,
        "places_found": len(data),
        "avg_field_fill": avg_fill,
        "names": names[:3],
    }


# ---------------------------------------------------------------------------
# Run a single model × task
# ---------------------------------------------------------------------------

def run_test_ollama(model: str, task: str, prompt: str, host: str) -> TestResult:
    result = TestResult(model=model, task=task, elapsed_s=0.0)
    t0 = time.monotonic()
    try:
        import ollama
        client = ollama.Client(host=host)
        response = client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            format="json",
        )
        result.elapsed_s = time.monotonic() - t0
        content = response.message.content
        if content is None:
            result.error = "null content"
            return result
        result.raw = content
        result.parsed = _parse(content)
        if result.parsed is None:
            result.error = "JSON parse failed"
        elif task == "metadata":
            result.scores = _score_metadata(result.parsed)
        else:
            result.scores = _score_places(result.parsed)
    except Exception as exc:
        result.elapsed_s = time.monotonic() - t0
        result.error = str(exc)[:120]
    return result


def run_test_openrouter(model: str, task: str, prompt: str, api_key: str) -> TestResult:
    result = TestResult(model=model, task=task, elapsed_s=0.0)
    t0 = time.monotonic()
    try:
        from openai import OpenAI, BadRequestError, NotFoundError, RateLimitError
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            default_headers={
                "HTTP-Referer": "https://github.com/zeitungsarchiv",
                "X-Title": "Zeitungsarchiv",
            },
        )
        completion = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        result.elapsed_s = time.monotonic() - t0
        if not completion.choices:
            result.error = "empty choices"
            return result
        content = completion.choices[0].message.content
        if content is None:
            result.error = "null content"
            return result
        result.raw = content
        result.parsed = _parse(content)
        if result.parsed is None:
            result.error = f"JSON parse failed (raw: {content[:80]!r})"
        elif task == "metadata":
            result.scores = _score_metadata(result.parsed)
        else:
            result.scores = _score_places(result.parsed)
    except Exception as exc:
        result.elapsed_s = time.monotonic() - t0
        result.error = str(exc)[:120]
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

OPENROUTER_MODELS = [
    "nvidia/nemotron-3-super-120b-a12b:free",
    "google/gemma-4-31b-it:free",
    "minimax/minimax-m2.5:free",
    "z-ai/glm-4.5-air:free",
    "meta-llama/llama-3.1-8b-instruct:free",
]

OLLAMA_MODELS = [
    "minimax-m2.5:cloud",
    "gemma4:e2b",
    "deepseek-coder-v2:16b",
]

TASKS = [
    ("metadata", METADATA_PROMPT.format(ocr_text=OCR_METADATA_TEST)),
    ("places",   PLACES_PROMPT.format(ocr_text=OCR_PLACES_TEST)),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark LLM models for extraction quality")
    parser.add_argument("--provider", choices=["ollama", "openrouter"], default="ollama")
    parser.add_argument("--host", default="http://localhost:11434", help="Ollama host")
    parser.add_argument("--api-key", default=os.getenv("OPENROUTER_API_KEY", ""),
                        help="OpenRouter API key (or set OPENROUTER_API_KEY)")
    parser.add_argument("--models", nargs="+",
                        help="Models to test (default: provider-specific list)")
    args = parser.parse_args()

    if args.provider == "openrouter" and not args.api_key:
        parser.error("--api-key or OPENROUTER_API_KEY required for openrouter provider")

    models = args.models or (OPENROUTER_MODELS if args.provider == "openrouter" else OLLAMA_MODELS)

    results: list[TestResult] = []

    for model in models:
        print(f"\n{'='*60}")
        print(f"MODEL: {model}  [{args.provider}]")
        print("="*60)
        for task_name, prompt in TASKS:
            print(f"  Running {task_name}...", end="", flush=True)
            if args.provider == "openrouter":
                r = run_test_openrouter(model, task_name, prompt, args.api_key)
            else:
                r = run_test_ollama(model, task_name, prompt, args.host)
            results.append(r)
            if r.ok:
                print(f" {r.elapsed_s:.1f}s  OK")
            else:
                print(f" FAIL: {r.error}")
            print(r.summary_line())
            if r.ok and r.parsed:
                preview = json.dumps(r.parsed, ensure_ascii=False)[:200]
                print(indent(preview, "    "))

    # Summary table
    print(f"\n{'='*60}")
    print("SUMMARY")
    print("="*60)
    print(f"{'Model':<30} {'Task':<10} {'Time':>6}  {'Result'}")
    print("-"*70)
    for r in results:
        status = "OK" if r.ok else f"FAIL({r.error[:30]})"
        score_str = "  ".join(f"{k}={v}" for k, v in r.scores.items())
        print(f"{r.model:<30} {r.task:<10} {r.elapsed_s:>5.1f}s  {status}  {score_str}")

    # Ranking
    print(f"\n{'='*60}")
    print("RANKING (by JSON validity + field completeness)")
    print("="*60)
    model_scores: dict[str, list] = {}
    for r in results:
        model_scores.setdefault(r.model, []).append(r)

    ranked = []
    for model, model_results in model_scores.items():
        ok_count = sum(1 for r in model_results if r.ok)
        avg_time = sum(r.elapsed_s for r in model_results) / len(model_results)
        ranked.append((ok_count, -avg_time, model))

    ranked.sort(reverse=True)
    for i, (ok, neg_time, model) in enumerate(ranked, 1):
        print(f"  #{i}: {model:<30}  tasks_ok={ok}/{len(TASKS)}  avg_time={-neg_time:.1f}s")


if __name__ == "__main__":
    main()
