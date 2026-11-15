"""Every environment variable the code reads must be documented in .env.example.

`README.md` documented 35 of the 111 variables the code reads, and `.env.example`
listed 37 — leaving 74 live knobs (AGENT_PARALLEL, AGENT_REFLECT, MCP_TIMEOUT,
STREAM_MAX_DURATION, ...) that changed runtime behaviour and appeared in no file a
user would look at.

This test extracts variables the same way the fix did: from the source, not from a
hand-maintained list. A variable added to the code without a line in
`.env.example` fails here rather than becoming invisible.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent

# read indirectly at runtime by modules/model_tiers.py, which maps a provider to
# its two env-var NAMES. A os.getenv() scan cannot see these, so they are listed
# explicitly rather than silently skipped.
DYNAMIC = {
    "GROQ_MODEL_STRONG", "GROQ_MODEL_FAST",
    "OPENAI_MODEL_STRONG", "OPENAI_MODEL_FAST", "OPENAI_API_KEY",
    "ANTHROPIC_MODEL_STRONG", "ANTHROPIC_MODEL_FAST", "ANTHROPIC_API_KEY",
}

# Not configuration: written by GitHub Actions for any step, and read by the eval
# runner to build its job summary.
PROVIDED_BY_ENVIRONMENT = {"GITHUB_STEP_SUMMARY"}


def _read_env_vars():
    found = set()
    for p in ROOT.rglob("*.py"):
        if any(x in p.parts for x in ("tests", ".git", "node_modules", ".venv")):
            continue
        src = p.read_text(encoding="utf-8", errors="ignore")
        found |= set(re.findall(
            r'os\.(?:getenv|environ\.get)\(\s*["\']([A-Z][A-Z0-9_]*)["\']', src))
        found |= set(re.findall(
            r'os\.environ\[\s*["\']([A-Z][A-Z0-9_]*)["\']\s*\]', src))
    return found


def _documented():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    # a variable counts as documented whether it is live or commented out
    return set(re.findall(r'^\s*#?\s*([A-Z][A-Z0-9_]*)=', text, re.M))


def test_every_env_var_the_code_reads_is_in_env_example():
    read = _read_env_vars() - PROVIDED_BY_ENVIRONMENT
    documented = _documented()
    missing = sorted(read - documented)
    assert not missing, (
        "environment variables read by the code but absent from .env.example: "
        + ", ".join(missing)
    )


def test_dynamically_read_provider_vars_are_documented():
    """model_tiers.py reads these by name lookup, so the scan above cannot see them."""
    missing = sorted(DYNAMIC - _documented())
    assert not missing, f"dynamically read but undocumented: {missing}"


def _mentioned_anywhere():
    """Every variable NAME appearing in any source file, however it is read.

    Necessary because several are read through a variable-name indirection that a
    literal os.getenv("X") scan cannot see:

        _max_tokens_env = "AGENT_MAX_TOKENS"; os.getenv(_max_tokens_env)
        _PROVIDER_ENV = {"groq": ("GROQ_MODEL_STRONG", ...)}; os.getenv(name)

    and `SSL_CN` is read by config/nginx/ensure-cert.sh (shell), not Python at all.
    """
    found = set()
    for pattern in ("*.py", "*.sh", "*.yml", "*.yaml", "*.conf"):
        for p in ROOT.rglob(pattern):
            if any(x in p.parts for x in (".git", "node_modules", ".venv", "tests")):
                continue
            src = p.read_text(encoding="utf-8", errors="ignore")
            found |= set(re.findall(r'\b([A-Z][A-Z0-9_]{2,})\b', src))
    return found


def test_env_example_does_not_promise_vars_nothing_reads():
    """The reverse direction: a line in .env.example that nothing reads is a lie."""
    read = _mentioned_anywhere()
    documented = _documented()
    secrets_src = (ROOT / "modules/secrets.py").read_text(encoding="utf-8")
    # `*_FILE` secret variants are built as f"{env_var}_FILE" in secrets.py
    file_variants = {v + "_FILE" for v in _read_env_vars()}
    unread = sorted(
        v for v in documented - read
        if v not in file_variants
        and f'"{v}"' not in secrets_src
    )
    assert not unread, f".env.example documents variables nothing reads: {unread}"
