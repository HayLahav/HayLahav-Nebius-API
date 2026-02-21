import os
import re
import json
import httpx
from openai import OpenAI, AuthenticationError, APIConnectionError, APIStatusError
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="GitHub Repository Summarizer")

NEBIUS_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"
NEBIUS_MODEL = "meta-llama/Meta-Llama-3.1-8B-Instruct"

RAW_BASE = "https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
GITHUB_API_BASE = "https://api.github.com"

PRIORITY_FILES = [
    "README.md",
    "README.rst",
    "README.txt",
    "pyproject.toml",
    "requirements.txt",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "setup.py",
    "setup.cfg",
]

SOURCE_EXTENSIONS = {
    ".py", ".js", ".ts", ".go", ".rs", ".java", ".rb",
    ".cpp", ".c", ".cs", ".swift", ".kt", ".scala", ".php",
    ".vue", ".jsx", ".tsx",
}

# Common entry-point filenames tried first when sampling source files
SOURCE_ENTRY_POINTS = [
    "main.py", "app.py", "server.py", "index.py",
    "main.js", "app.js", "server.js", "index.js",
    "main.ts", "app.ts", "server.ts", "index.ts",
    "main.go",
    "main.rs",
    "main.java",
    "main.rb", "app.rb",
]

MAX_CONTENT_CHARS = 12000
MAX_SOURCE_FILE_CHARS = 3000  # cap per sampled source file


class SummarizeRequest(BaseModel):
    github_url: str


def parse_github_url(url: str) -> tuple[str, str]:
    pattern = r"https?://github\.com/([^/]+)/([^/?\s#]+)"
    match = re.match(pattern, url.strip())
    if not match:
        raise ValueError("Invalid GitHub URL...")
    owner = match.group(1)
    repo = match.group(2).rstrip("/")
    if repo.endswith(".git"):
        repo = repo[:-4]
    return owner, repo


def _github_headers() -> dict:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_repo_metadata(owner: str, repo: str) -> dict:
    """
    Returns {"default_branch": str, "entries": [{"name": str, "type": "file"|"dir"}, ...]}.
    Uses the GitHub REST API instead of HTML scraping.
    """
    headers = _github_headers()

    # 1. Resolve the default branch
    default_branch = "main"
    try:
        resp = httpx.get(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}",
            headers=headers,
            timeout=10,
            follow_redirects=True,
        )
        if resp.status_code == 200:
            default_branch = resp.json().get("default_branch", "main")
    except httpx.RequestError:
        pass

    # 2. List root directory contents
    entries: list[dict] = []
    try:
        resp = httpx.get(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/",
            headers=headers,
            timeout=10,
            follow_redirects=True,
        )
        if resp.status_code == 200:
            for item in resp.json():
                entries.append({"name": item["name"], "type": item["type"]})
    except (httpx.RequestError, ValueError):
        pass

    return {"default_branch": default_branch, "entries": entries}


def fetch_raw_file(owner: str, repo: str, path: str, branch: str) -> str | None:
    url = RAW_BASE.format(owner=owner, repo=repo, branch=branch, path=path)
    try:
        resp = httpx.get(url, timeout=10, follow_redirects=True)
        if resp.status_code == 200:
            return resp.text
    except httpx.RequestError:
        pass
    return None


def pick_source_files(entries: list[dict]) -> list[str]:
    """
    From root directory entries, return up to 2 source file paths to sample.
    Prefers known entry-point names; falls back to any file with a source extension.
    """
    file_names = {e["name"] for e in entries if e["type"] == "file"}

    chosen: list[str] = []
    for name in SOURCE_ENTRY_POINTS:
        if name in file_names:
            chosen.append(name)
        if len(chosen) >= 2:
            break

    if not chosen:
        for name in sorted(file_names):
            ext = os.path.splitext(name)[1].lower()
            if ext in SOURCE_EXTENSIONS:
                chosen.append(name)
            if len(chosen) >= 2:
                break

    return chosen


def collect_repo_content(owner: str, repo: str) -> dict:
    meta = fetch_repo_metadata(owner, repo)
    branch = meta["default_branch"]
    entries = meta["entries"]

    files: dict[str, str] = {}
    for filename in PRIORITY_FILES:
        content = fetch_raw_file(owner, repo, filename, branch)
        if content:
            files[filename] = content

    source_files: dict[str, str] = {}
    for path in pick_source_files(entries):
        content = fetch_raw_file(owner, repo, path, branch)
        if content:
            source_files[path] = content[:MAX_SOURCE_FILE_CHARS]

    return {
        "files": files,
        "source_files": source_files,
        "directory": [e["name"] for e in entries[:40]],
    }


def build_context(data: dict) -> str:
    parts = []
    budget = MAX_CONTENT_CHARS

    files: dict[str, str] = data.get("files", {})
    source_files: dict[str, str] = data.get("source_files", {})
    directory: list[str] = data.get("directory", [])

    if directory:
        dir_text = "Root directory entries:\n" + "\n".join(f"  {e}" for e in directory)
        parts.append(dir_text)
        budget -= len(dir_text)

    # README first, then other manifest/config files
    ordered = []
    for name in PRIORITY_FILES:
        if name in files:
            ordered.append((name, files[name]))

    for name, content in ordered:
        if budget <= 0:
            break
        header = f"\n--- {name} ---\n"
        available = budget - len(header)
        if available <= 0:
            break
        snippet = content[:available]
        parts.append(header + snippet)
        budget -= len(header) + len(snippet)

    # Sampled source files fill whatever budget remains
    for name, content in source_files.items():
        if budget <= 0:
            break
        header = f"\n--- {name} (source sample) ---\n"
        available = budget - len(header)
        if available <= 0:
            break
        snippet = content[:available]
        parts.append(header + snippet)
        budget -= len(header) + len(snippet)

    return "\n".join(parts)


def call_nebius(context: str, owner: str, repo: str) -> dict:
    api_key = os.environ.get("NEBIUS_API_KEY", "").strip()
    if not api_key:
        raise EnvironmentError("NEBIUS_API_KEY environment variable is not set.")

    client = OpenAI(
        base_url=NEBIUS_BASE_URL,
        api_key=api_key
    )

    prompt = f"""You are a code analyst. Analyze the following GitHub repository context for {owner}/{repo} and return ONLY valid JSON with exactly these fields:
- "summary": a 2-4 sentence description of what the project does
- "technologies": a JSON array of main languages, frameworks, and libraries used
- "structure": a 1-2 sentence description of the project layout

Repository context:
{context}

Respond with ONLY a JSON object, no markdown, no extra text."""

    try:
        response = client.chat.completions.create(
            model=NEBIUS_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }
            ],
            temperature=0.2,
            max_tokens=800,
        )
    except AuthenticationError:
        raise PermissionError("Invalid NEBIUS_API_KEY.")
    except APIConnectionError as e:
        raise ConnectionError(f"Failed to reach Nebius API: {e}")
    except APIStatusError as e:
        raise RuntimeError(f"Nebius API returned {e.status_code}: {e.message}")

    text = response.choices[0].message.content.strip()

    # Strip markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    return json.loads(text)


@app.post("/summarize")
def summarize(request: SummarizeRequest):
    try:
        owner, repo = parse_github_url(request.github_url)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})

    try:
        data = collect_repo_content(owner, repo)
    except Exception as e:
        return JSONResponse(status_code=502, content={"status": "error", "message": f"Failed to fetch repository: {e}"})

    if not data["files"] and not data["directory"] and not data["source_files"]:
        return JSONResponse(status_code=404, content={
            "status": "error",
            "message": "Repository not found or is empty.",
        })

    context = build_context(data)

    try:
        result = call_nebius(context, owner, repo)
    except EnvironmentError as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
    except PermissionError as e:
        return JSONResponse(status_code=401, content={"status": "error", "message": str(e)})
    except (ConnectionError, RuntimeError) as e:
        return JSONResponse(status_code=502, content={"status": "error", "message": str(e)})
    except json.JSONDecodeError:
        return JSONResponse(status_code=502, content={"status": "error", "message": "LLM returned invalid JSON."})

    return {
        "summary": result.get("summary", ""),
        "technologies": result.get("technologies", []),
        "structure": result.get("structure", ""),
    }
