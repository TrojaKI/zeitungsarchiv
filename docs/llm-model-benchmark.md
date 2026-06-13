# LLM Model Benchmark — Zeitungsarchiv

Benchmark-Datum: 2026-04-23

Getestet wurden Metadata- und Places-Extraktion mit echten OCR-Texten aus dem Archiv.
Bewertet: JSON-Validität, Feldvollständigkeit (6/6 Pflichtfelder), korrekte Kategorie, Antwortzeit.

---

## Ollama (lokal / cloud-relay)

| Modell | Metadata | Places | Ø Zeit | Anmerkung |
|---|---|---|---|---|
| `minimax-m2.5:cloud` | ✅ 6/6, Kategorie OK | ✅ 3 Orte, 73% Fellfüllung | **6s** | Bestes Ergebnis gesamt |
| `deepseek-coder-v2:16b` | ✅ 6/6, Kategorie OK | ❌ 0 Orte (JSON invalid) | 57s | Places unbrauchbar |
| `gemma4:e2b` | ✅ 6/6, Kategorie OK | ✅ 3 Orte, 73% Fellfüllung | 231s | Qualität gut, zu langsam |
| `kimi-k2.6:cloud` | ❌ Abo erforderlich | ❌ Abo erforderlich | — | Ollama Pro nötig |
| `glm-5:cloud` | ❌ Abo erforderlich | ❌ Abo erforderlich | — | Ollama Pro nötig |

**Konfiguration (`OLLAMA_MODELS`):**
```
minimax-m2.5:cloud,gemma4:e2b
```
`minimax-m2.5:cloud` als Primär, `gemma4:e2b` als lokaler Offline-Fallback.

---

## OpenRouter (free tier)

| Modell | Metadata | Places | Ø Zeit | Anmerkung |
|---|---|---|---|---|
| `nvidia/nemotron-3-super-120b-a12b:free` | ✅ 6/6, Kategorie OK | ✅ 3 Orte, 73% | **39s** | Bestes OpenRouter-Modell |
| `z-ai/glm-4.5-air:free` | ✅ 6/6 (newspaper manchmal null) | ✅ 3 Orte, 73% | 77s | Zuverlässig |
| `minimax/minimax-m2.5:free` | ✅ 6/6, Kategorie OK | ✅ 3 Orte, 73% | 138s | Langsam aber korrekt |
| `google/gemma-4-31b-it:free` | ❌ 429 Rate-Limit | ❌ 429 Rate-Limit | — | Dauerhaft gedrosselt |
| `meta-llama/llama-3.1-8b-instruct:free` | ❌ 404 Not Found | ❌ 404 Not Found | — | Deprecated / entfernt |

**Konfiguration (`OPENROUTER_MODELS`):**
```
nvidia/nemotron-3-super-120b-a12b:free,z-ai/glm-4.5-air:free,minimax/minimax-m2.5:free
```

---

## Gesamtranking

| # | Modell | Provider | Ø Zeit | Ergebnis |
|---|---|---|---|---|
| 1 | `minimax-m2.5:cloud` | Ollama | 6s | ✅ Alle Felder, schnellstes |
| 2 | `nvidia/nemotron-3-super-120b-a12b:free` | OpenRouter | 39s | ✅ Alle Felder |
| 3 | `z-ai/glm-4.5-air:free` | OpenRouter | 77s | ✅ newspaper gelegentlich null |
| 4 | `minimax/minimax-m2.5:free` | OpenRouter | 138s | ✅ Alle Felder, langsam |
| 5 | `deepseek-coder-v2:16b` | Ollama | 57s | ⚠️ Places-JSON defekt |
| 6 | `gemma4:e2b` | Ollama | 231s | ✅ Korrekt, aber zu langsam |

---

## Empfehlung

- **Produktiv (Ollama):** `LLM_PROVIDER=ollama`, `OLLAMA_MODELS=minimax-m2.5:cloud,gemma4:e2b`
- **Produktiv (OpenRouter):** `LLM_PROVIDER=openrouter`, Fallback-Kette `nemotron → glm-4.5-air → minimax-m2.5`
- **`deepseek-coder-v2:16b`** nicht für Places-Extraktion geeignet
- **`google/gemma-4-31b-it:free`** und **`meta-llama/llama-3.1-8b-instruct:free`** aus OpenRouter-Liste entfernen
