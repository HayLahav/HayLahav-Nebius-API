# GitHub Repository Summarizer API

A simple FastAPI service that takes a public GitHub repository URL and returns a human-readable summary of what the project does, which technologies it uses, and how it is structured.

## Setup

### 1. Clone the repository

```bash
git clone <repo-url>
cd HayLahav-Nebius-API
```

### 2. Create and activate a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate   # Linux / macOS
# venv\Scripts\activate    # Windows
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Set your Nebius API key

```bash
export NEBIUS_API_KEY="your-nebius-api-key-here"
```

### 5. Start the server

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

The API will be available at `http://localhost:8000`.

## Usage

```bash
curl -X POST http://localhost:8000/summarize \
  -H "Content-Type: application/json" \
  -d '{"github_url": "https://github.com/psf/requests"}'
```

### Example response

```json
{
  "summary": "Requests is a popular Python HTTP library that makes sending HTTP/1.1 requests extremely simple...",
  "technologies": ["Python", "urllib3", "certifi", "chardet"],
  "structure": "The project follows a standard Python package layout with source code in src/requests/, tests in tests/, and documentation in docs/."
}
```

### Error response

```json
{
  "status": "error",
  "message": "NEBIUS_API_KEY environment variable is not set."
}
```

## Design Decisions

**Framework:** FastAPI was chosen for its automatic request validation via Pydantic, built-in OpenAPI docs at `/docs`, and clean async-ready structure — all with minimal boilerplate.

**Model:** `meta-llama/Meta-Llama-3.1-70B-Instruct` was selected because it offers strong instruction-following and structured JSON output at a low cost, which is ideal for a single-call summarization task where token efficiency matters.

**Repository filtering strategy:** The service fetches only high-signal files — README (primary documentation), dependency manifests (`requirements.txt`, `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`), and the root directory listing from the GitHub HTML page. Binary files, lock files, `node_modules/`, and source code files are intentionally skipped: they add noise or exceed context limits without improving summary quality. The total content sent to the LLM is capped at 6 000 characters, with README content prioritized first, followed by config files, and then truncated if necessary.
