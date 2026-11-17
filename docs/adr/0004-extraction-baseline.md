# ADR 0004: Two baseline extractors with schema-constrained output, scored offline

**Status:** Accepted

**Context:** The agent in Phase 5 needs a baseline to beat, and the main design choice is how the LLM sees the document: as text rebuilt from the text layer or OCR, or as a page image.

**Options:**

- Input: page text (text layer or OCR); page image to a vision LLM; trained layout models such as LayoutLM or Donut; templates and regular expressions.
- Output: free text parsed afterwards; JSON mode; JSON constrained by a schema.

**Decision:**

- Build both LLM methods, `text` and `vision`, with the same rules, schema, and parser, so the only difference between them is the input. Templates break on new layouts, and trained layout models need labelled data and a GPU, which Phase 10 revisits.
- Constrain output with a JSON schema derived from the Pydantic models. References are inlined, every key is required and nullable, and numbers are strings, so no amount passes through a float.
- The prompt says to copy, never calculate or correct. Arithmetic checks belong to validation (Phase 5), and a model that "fixes" totals would hide the errors validation exists to find. Document text is fenced and marked as data.
- Use temperature 0 and render vision pages at 150 DPI. No repair loop yet, so the parse failure rate is measured before anything hides it.
- Save each prediction as one JSONL line with its provider, model, prompt version, tokens, and timing. Runs resume after interruptions, and scoring never repeats paid calls.
- Use Gemini through plain HTTP, with Ollama as a local option behind the same interface.

**Consequences:** Real numbers need a Gemini key. Free-tier content may be used by Google and read by reviewers, which is acceptable for synthetic invoices but not for real ones; those should use Ollama or a paid tier. Thinking tokens are counted as output, because they are billed. The text method inherits OCR misreads, such as `TI` read as `Tl`, which no prompt can undo. The vision method avoids OCR, but its token cost per page depends on how the provider tiles images, so it is measured rather than assumed.

**Update, 2026-11-07, model choice:** Pin `gemini-3.5-flash-lite`. The default alias `gemini-flash-latest` returned HTTP 503 ("high demand") or hung until the 120-second timeout, as did `gemini-3.8-flash` and `gemini-3.7-flash`. `gemini-2.5-flash` returned 404 for new users. `gemini-3.5-flash` worked but took 17 seconds and 81 thinking tokens to answer "ok", while `gemini-3.5-flash-lite` answered in under 2 seconds with no thinking tokens. Pinned ids go stale, so the model stays configurable with `GEMINI_MODEL`, and every prediction records the model version that actually answered.
