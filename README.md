# GitHub Repository Summarizer API

A simple **FastAPI** service that takes a public GitHub repository URL
and returns a human-readable summary of:

-   What the project does\
-   Which technologies it uses\
-   How it is structured

------------------------------------------------------------------------

## 🚀 Setup

### 1. Clone the repository

``` bash
git clone https://github.com/HayLahav/HayLahav-Nebius-API
cd HayLahav-Nebius-API
```

### 2. Create and activate a virtual environment

#### Windows (PowerShell)

``` powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

#### Linux / macOS

``` bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

``` bash
pip install -r requirements.txt
```

### 4. Set your Nebius API key

#### Windows (PowerShell)

``` powershell
$env:NEBIUS_API_KEY="your-nebius-api-key-here"
```

#### Linux / macOS

``` bash
export NEBIUS_API_KEY="your-nebius-api-key-here"
```

**Optional:** Set a GitHub personal access token to raise the GitHub API rate limit from 60 to 5,000 requests/hour:

``` bash
export GITHUB_TOKEN="your-github-token-here"
```

### 5. Start the server

``` bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

The API will be available at:

-   http://localhost:8000\
-   Interactive Swagger UI: http://localhost:8000/docs

------------------------------------------------------------------------


## 📌 Usage
Note: Keep the server running in your first terminal. Open a new terminal window (or tab) to run the following test commands.

### Using PowerShell

``` powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/summarize" `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"github_url": "https://github.com/fill_here_url"}' | ConvertTo-Json
```

### Using cURL

``` bash
curl -X POST http://localhost:8000/summarize   -H "Content-Type: application/json"   -d '{"github_url": "https://github.com/psf/requests"}'
```

------------------------------------------------------------------------

## 📦 Example JSON Response
``` bash
curl -X POST http://localhost:8000/summarize \
  -H "Content-Type: application/json" \
  -d '{"github_url": "https://github.com/HayLahav/HayLahav-Nebius-API"}'
```

``` json
{
    "summary": "A simple FastAPI service that takes a public GitHub repository URL and returns a human-readable summary of what the project does, which technologies it uses, and how it is structured.",
    "technologies": [
        "Python",
        "FastAPI",
        "uvicorn",
        "httpx",
        "OpenAI",
        "Pydantic"
    ],
    "structure": "The project follows a standard Python package layout with source code in main.py, dependencies in requirements.txt, and documentation in README.md."
}
```

------------------------------------------------------------------------

## 🧠 Design Decisions

### Framework

**FastAPI** was chosen because:

-   Automatic request validation via Pydantic\
-   Built-in OpenAPI documentation at /docs\
-   Clean, async-ready architecture\
-   Excellent performance

------------------------------------------------------------------------

### Model

`meta-llama/Meta-Llama-3.1-8B-Instruct` (via Nebius Token Factory) was
selected because:

-   Extremely fast inference\
-   Strong instruction-following capabilities\
-   Reliable structured JSON generation\
-   Large 128k token context window

------------------------------------------------------------------------

### Repository Filtering Strategy

To reduce noise and improve summary quality, the service fetches only
high-signal files.

#### Primary Documentation

-   README (.md, .rst, .txt)

#### Manifests

-   requirements.txt\
-   pyproject.toml\
-   package.json\
-   Cargo.toml\
-   go.mod\
-   Other dependency/configuration files

#### Directory Layout

-   Uses the **GitHub REST API** (`/repos/{owner}/{repo}/contents/`) instead of HTML scraping\
-   Also resolves the repository's actual **default branch** via the API, eliminating the `main`/`master` guessing loop\
-   Helps infer project organization and architecture

#### Source File Sampling

-   After fetching manifests, the service picks up to **2 source files** from the root directory\
-   Prefers well-known entry points (`main.py`, `app.js`, `main.go`, etc.); falls back to any file with a recognised source extension\
-   Each sampled file is capped at **3,000 characters** so it fits within the overall context budget\
-   Gives the LLM actual code signal for repos with thin or missing documentation

------------------------------------------------------------------------

### Context Management Strategy

-   Total content sent to the LLM is capped at **12,000 characters**\
-   Content is added in priority order:
    1.  Root directory listing (cheap, high-signal structural overview)
    2.  README (highest-signal documentation)
    3.  Manifest / config files (`requirements.txt`, `package.json`, etc.)
    4.  Sampled source files (up to 3,000 chars each, fills remaining budget)\
-   Ensures relevant architectural information is preserved\
-   Prevents unnecessary latency and excessive token usage

------------------------------------------------------------------------

## 🛠 Tech Stack

-   Python\
-   FastAPI\
-   Uvicorn\
-   httpx\
-   Pydantic\
-   Nebius Token Factory\
-   Meta Llama 3.1 8B Instruct
