import os
import re
import json
import httpx
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="GitHub Repository Summarizer")

NEBIUS_API_URL = "https://api.studio.nebius.com/v1/chat/completions"
NEBIUS_MODEL = "meta-llama/Meta-Llama-3.1-70B-Instruct"

RAW_BASE = "https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
GITHUB_REPO_URL = "https://github.com/{owner}/{repo}"

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

MAX_CONTENT_CHARS = 6000


class SummarizeRequest(BaseModel):
    github_url: str


def parse_github_url(url: str) -> tuple[str, str]:
    pattern = r"https?://github\.com/([^/]+)/([^/?\s#]+)"
    match = re.match(pattern, url.strip())
    if not match:
        raise ValueError("Invalid GitHub URL. Expected format: https://github.com/owner/repo")
    owner = match.group(1)
    repo = match.group(2).rstrip("/")
    return owner, repo


def fetch_raw_file(owner: str, repo: str, path: str, branch: str = "main") -> str | None:
    for b in [branch, "master"]:
        url = RAW_BASE.format(owner=owner, repo=repo, branch=b, path=path)
        try:
            resp = httpx.get(url, timeout=10, follow_redirects=True)
            if resp.status_code == 200:
                return resp.text
        except httpx.RequestError:
            continue
    return None


def fetch_directory_listing(owner: str, repo: str) -> list[str]:
    url = GITHUB_REPO_URL.format(owner=owner, repo=repo)
    try:
        resp = httpx.get(url, timeout=10, follow_redirects=True)
        if resp.status_code != 200:
            return []
    except httpx.RequestError:
        return []

    # Extract top-level file/dir names from the repo page HTML
    entries = re.findall(
        r'aria-label="([^"]+?)(?:,\s*(?:directory|file))?"',
        resp.text,
    )
    # Also try the link-based approach as fallback
    if not entries:
        entries = re.findall(
            r'href="/{owner}/{repo}/(?:tree|blob)/[^/]+/([^/"?]+)"'.format(
                owner=re.escape(owner), repo=re.escape(repo)
            ),
            resp.text,
        )
    seen = set()
    result = []
    for e in entries:
        name = e.strip()
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return result[:40]


def collect_repo_content(owner: str, repo: str) -> dict:
    files: dict[str, str] = {}

    for filename in PRIORITY_FILES:
        content = fetch_raw_file(owner, repo, filename)
        if content:
            files[filename] = content

    directory_entries = fetch_directory_listing(owner, repo)
    return {"files": files, "directory": directory_entries}


def build_context(data: dict) -> str:
    parts = []
    budget = MAX_CONTENT_CHARS

    files: dict[str, str] = data.get("files", {})
    directory: list[str] = data.get("directory", [])

    if directory:
        dir_text = "Root directory entries:\n" + "\n".join(f"  {e}" for e in directory)
        parts.append(dir_text)
        budget -= len(dir_text)

    # Prioritize README first, then config files
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

    return "\n".join(parts)


def call_nebius(context: str, owner: str, repo: str) -> dict:
    api_key = os.environ.get("NEBIUS_API_KEY", "").strip()
    if not api_key:
        raise EnvironmentError("NEBIUS_API_KEY environment variable is not set.")

    prompt = f"""You are a code analyst. Analyze the following GitHub repository context for {owner}/{repo} and return ONLY valid JSON with exactly these fields:
- "summary": a 2-4 sentence description of what the project does
- "technologies": a JSON array of main languages, frameworks, and libraries used
- "structure": a 1-2 sentence description of the project layout

Repository context:
{context}

Respond with ONLY a JSON object, no markdown, no extra text."""

    payload = {
        "model": NEBIUS_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 512,
    }

    try:
        resp = httpx.post(
            NEBIUS_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
    except httpx.RequestError as e:
        raise ConnectionError(f"Failed to reach Nebius API: {e}")

    if resp.status_code == 401:
        raise PermissionError("Invalid NEBIUS_API_KEY.")
    if resp.status_code != 200:
        raise RuntimeError(f"Nebius API returned {resp.status_code}: {resp.text[:200]}")

    raw = resp.json()
    text = raw["choices"][0]["message"]["content"].strip()

    # Strip markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    return json.loads(text)


@app.post("/summarize")
def summarize(request: SummarizeRequest):
    try:
        owner, repo = parse_github_url(request.github_url)
    except ValueError as e:
        return {"status": "error", "message": str(e)}, 400

    try:
        data = collect_repo_content(owner, repo)
    except Exception as e:
        return {"status": "error", "message": f"Failed to fetch repository: {e}"}, 502

    if not data["files"] and not data["directory"]:
        return {
            "status": "error",
            "message": "Repository not found or is empty.",
        }, 404

    context = build_context(data)

    try:
        result = call_nebius(context, owner, repo)
    except EnvironmentError as e:
        return {"status": "error", "message": str(e)}, 500
    except PermissionError as e:
        return {"status": "error", "message": str(e)}, 401
    except (ConnectionError, RuntimeError) as e:
        return {"status": "error", "message": str(e)}, 502
    except json.JSONDecodeError:
        return {"status": "error", "message": "LLM returned invalid JSON."}, 502

    return {
        "summary": result.get("summary", ""),
        "technologies": result.get("technologies", []),
        "structure": result.get("structure", ""),
    }
