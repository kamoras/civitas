# Modern Punk - Project Mandates

This project is a "Modern Punk" themed political representation tracker. It provides a data-intensive, AI-powered analysis of US politicians (Senators, Representatives, Presidents, and Justices) with a terminal-inspired aesthetic.

## 🛠 Tech Stack & Architecture

### Backend (Python/FastAPI)
- **Framework:** FastAPI with Pydantic 2.0+ for validation.
- **Database:** SQLite via SQLAlchemy 2.0 (Mapped/Declarative style).
- **AI Integration:** 
    - **LLM:** `llama-server` / `ollama` for classification, summarization, and reasoning.
    - **Vector Store:** ChromaDB for semantic search and embedding-based classification.
    - **Active Learning:** `LearnedClassification` table persists entity classifications (orgs, PACs) to minimize redundant LLM calls.
- **Data Pipeline:** A unified 7-phase orchestrator (`backend/app/pipeline/orchestrator.py`):
    1. **FETCH** (Raw data from Congress.gov, FEC, GovInfo)
    2. **TRANSFORM** (Normalize members, votes, and finance data)
    3. **ANALYZE** (Hybrid classification: rules -> embeddings -> LLM)
    4. **EXPLORE** (Ingest government activity documents for semantic search)
    5. **JUSTICES** (SCOTUS justice scoring)
    6. **PRESIDENTS** (Presidential record analysis)
    7. **FINALIZE** (Persistence and trend snapshots)

### Frontend (TypeScript/Next.js)
- **Framework:** Next.js 14 (App Router).
- **Styling:** Tailwind CSS with a "Modern Punk" / "Terminal" aesthetic.
- **Visuals:** High-contrast colors, monospace fonts, scanlines, and glow effects.
- **Patterns:** Custom hooks for data fetching (`useSenators`, `useConfig`).

## 📜 Coding Mandates

### Backend Standards
- **Schemas:** All Pydantic schemas MUST inherit from `CamelModel` (defined in `backend/app/schemas.py`) to maintain camelCase on the wire while using snake_case internally.
- **Models:** Use SQLAlchemy 2.0 `Mapped` and `mapped_column` type annotations.
- **Pipeline Updates:** When modifying data ingestion, update the relevant phase in the 7-phase orchestrator. Never bypass the orchestrator's state tracking or locking mechanism.
- **Data Integrity:** `upsert_senator` logic replaces child records (donors, votes, etc.). Always ensure cascade deletes are handled correctly when modifying relationships.
- **AI/LLM Ethics:** AI-generated reasoning or summaries MUST be clearly attributed or cached in `AnalysisCache` with a `prompt_version`.

### Frontend Standards
- **Aesthetic Integrity:** Every new UI component MUST follow the terminal theme. Use `TerminalTitlebar`, `BranchSelector`, and existing layout primitives to maintain consistency.
- **Typography:** Prefer monospace fonts for data displays.
- **Responsiveness:** Components must handle terminal-like layouts on mobile.

### Development Workflow
- **Environment:** Requires `DATA_GOV_API_KEY` for fetching Congress/FEC data.
- **Testing:** New features MUST include Pytest cases in `backend/tests/`.
- **Validation:** Always verify pipeline changes by running a partial or full pipeline run (can be filtered by senator name for speed).

## 🛡 Security & Safety
- **Secrets:** Never log or commit API keys. Use `.env` for local development.
- **PII:** Be cautious with donor data; only use publicly available FEC data.
