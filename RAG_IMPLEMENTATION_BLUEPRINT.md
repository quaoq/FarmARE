# RAG Implementation Blueprint (When Documents Are Available)

This document describes a **conference-grade RAG plan** for FarmARE if/when you add document corpora (SOPs, equipment manuals, agronomy guides, incident reports, weather bulletins, etc.).

Today, FarmARE can run without RAG. This blueprint defines how to add it cleanly.

## 1) When RAG is justified

Use RAG only when you have non-trivial external knowledge (hundreds+ pages, changing policies, or long manuals) that agents cannot reliably keep in prompt context.

For FarmARE, examples:
- machinery operation manuals,
- pesticide/fertilizer policy docs,
- farm SOP and safety protocols,
- historical incident/postmortem notes.

## 2) Target architecture (SOTA-leaning, practical)

1. **Ingestion + indexing**
   - Parse PDFs/HTML/Markdown.
   - Chunk with structure-aware splitting (headings/tables preserved).
   - Store metadata: `doc_id`, section, timestamp/version, source URL, topic tags.

2. **Hybrid retrieval in Elasticsearch**
   - Sparse lexical retrieval (BM25) + dense vector retrieval (`knn`).
   - Fuse with **RRF** (Reciprocal Rank Fusion) for robust ranking.
   - Optional: add sparse semantic retriever (e.g., ELSER) as another retriever branch.

3. **Re-ranking**
   - Re-rank top candidates with cross-encoder or late-interaction ranker.
   - Keep top `N` passages for generation (typically 4–8).

4. **Grounded generation**
   - Inject retrieved passages into agent context.
   - Require evidence-linked output and source references in traces/artifacts.
   - Add “no evidence -> abstain/defer” behavior for high-risk actions.

5. **RAG telemetry + evaluation**
   - Retrieval metrics: Recall@k, MRR/nDCG.
   - QA metrics: answer correctness + faithfulness/groundedness.
   - Ops metrics: retrieval latency, index freshness/version, citation coverage.

## 3) Recommended retrieval stack details

### Hybrid query recipe
- Retriever A: BM25 (`standard`).
- Retriever B: dense vector kNN (`knn`).
- (Optional) Retriever C: sparse semantic retriever.
- Fusion: `rrf` retriever in Elasticsearch.

### Query-time improvements
- Multi-query expansion (2–4 rewrites).
- Optional HyDE-style synthetic passage for better dense retrieval in zero-shot cases.
- Domain filters by metadata (season, crop stage, machinery type, language, recency).

### Re-ranking
- Stage-2 reranker for precision at low k.
- Keep deterministic `top_k` and seed for reproducible experiments.

## 4) How this plugs into current FarmARE code

- Retrieval injection point: `are/simulation/agents/research_suite/research_agent.py`
- Skill-style retrieval precedent: `are/simulation/agents/research_suite/skill_library.py`
- Suite experiment wiring: `are/simulation/agent_suite/suite_runner.py`
- Config packs: `configs/agent_suite/*.yaml`

Implementation pattern:
1. Add a `documents_rag` config block to research family profile.
2. Add a retrieval client module (Elasticsearch adapter).
3. Inject retrieved passages into `_build_research_context(...)`.
4. Emit RAG telemetry in result metadata.
5. Add A/B packs: `rag_off` vs `rag_on_hybrid`.

## 5) Paper-safe experiment design

Report both:
- **Infra/readiness** (must pass): connectivity, index available, retrieval executes, traces exported.
- **Task metrics** (informational during smoke): workflow score/success.

For full experiments, include:
- family x scenario x (RAG OFF / RAG ON-hybrid) x (A2A OFF / ON),
- fixed seeds and repeats,
- fixed index snapshot hash/version.

## 6) Reproducibility rules

- Version every index build (doc hash + embedding model + chunking config).
- Freeze embedding/reranker model IDs per run.
- Store retrieval traces: query string, filters, top docs, scores, fused ranks.
- Keep citations in outputs for auditability.

## 7) Minimal phased rollout

Phase 1:
- Ingest docs, build ES hybrid index, simple RRF retrieval, prompt injection.

Phase 2:
- Add reranker + metadata filters + retrieval telemetry.

Phase 3:
- Add query rewriting/HyDE and rigorous ablations.

## 8) Primary references

- Retrieval-Augmented Generation (RAG): https://arxiv.org/abs/2005.11401  
- Active Retrieval-Augmented Generation (FLARE): https://arxiv.org/abs/2305.06983  
- Self-RAG: https://arxiv.org/abs/2310.11511  
- HyDE (Precise Zero-Shot Dense Retrieval): https://arxiv.org/abs/2212.10496  
- ColBERTv2 (late interaction retrieval): https://arxiv.org/abs/2112.01488  
- Elasticsearch kNN search docs: https://www.elastic.co/docs/solutions/search/vector/knn  
- Elasticsearch RRF docs: https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion
