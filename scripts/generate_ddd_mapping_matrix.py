#!/usr/bin/env python3
from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class SystemAnalysisEntry:
    source_path: str
    summary: str


SYSTEM_ANALYSIS_ROW_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*(.*?)\s*\|\s*$")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def parse_system_analysis(system_analysis_path: Path) -> tuple[list[SystemAnalysisEntry], str | None]:
    text = _read_text(system_analysis_path)
    m_time = re.search(r"^\-\s*生成时间:\s*(.+?)\s*$", text, flags=re.M)
    analysis_generated_at = m_time.group(1).strip() if m_time else None

    entries: list[SystemAnalysisEntry] = []
    for line in text.splitlines():
        m = SYSTEM_ANALYSIS_ROW_RE.match(line)
        if not m:
            continue
        entries.append(SystemAnalysisEntry(source_path=m.group(1).strip(), summary=m.group(2).strip()))
    return entries, analysis_generated_at


def extract_role_from_summary(summary: str) -> str:
    # Example: "位于 `...` 的后端入口/启动模块。导入/依赖: ..."
    m = re.search(r"的([^。]+?)。", summary)
    if m:
        return m.group(1).strip()
    return summary.split("。", 1)[0].strip()


def infer_context(source_path: str) -> str:
    if source_path.startswith("src/backend/"):
        return "Langflow"
    if source_path.startswith("src/lfx/"):
        return "LFX"
    return "Unknown"


def infer_service_name(context: str) -> str:
    return {"Langflow": "langflow", "LFX": "lfx"}.get(context, "unknown")


def compute_service_rel(source_path: str, context: str) -> str:
    p = source_path.replace("\\", "/")
    if context == "Langflow":
        for prefix in (
            "src/backend/base/langflow/",
            "src/backend/base/",
            "src/backend/langflow/",
            "src/backend/",
        ):
            if p.startswith(prefix):
                return p[len(prefix) :]
    if context == "LFX":
        for prefix in (
            "src/lfx/src/lfx/",
            "src/lfx/",
        ):
            if p.startswith(prefix):
                return p[len(prefix) :]
    return p


DDD_LAYER_CHILDREN: dict[str, set[str]] = {
    # From `ddd四层微服务目录结构-python.md` (allow project-specific deeper nesting under these roots).
    "presentation": {"api", "graphql", "grpc", "websocket", "cli", "dto", "assemblers"},
    "application": {
        "ports",
        "inbound_routing",
        "services",
        "workflows",
        "event_handlers",
        "transaction",
        "commands",
        "queries",
        "event_bus",
        "integration_events",
        "app_dto",
        "assemblers",
    },
    "domain": {
        "aggregates",
        "entities",
        "value_objects",
        "services",
        "events",
        "repositories",
        "specifications",
        "policies",
        "factories",
        "exceptions",
    },
    "infrastructure": {
        "persistence",
        "external_services",
        "caching",
        "monitoring",
        "container",
        "configuration",
        "messaging",
        "event_bus",
    },
}


def _choose_ddd_child_prefix(layer_dir: str, evidence: str) -> str:
    # Keep this strictly content-signal driven (evidence comes from source parsing), to follow AGENT.md rules.
    if layer_dir == "presentation":
        if evidence == "cli-adapter" or "typer" in evidence or "click" in evidence:
            return "cli"
        if evidence.startswith(("fastapi:", "class:http-adapter")):
            return "api"
        if evidence.startswith("model:"):
            return "dto"
        return "api"

    if layer_dir == "application":
        if evidence.startswith("port:"):
            return "ports"
        return "services"

    if layer_dir == "domain":
        if evidence.startswith("domain:exceptions"):
            return "exceptions"
        if evidence.startswith("port:"):
            return "repositories"
        if evidence.startswith(("domain:constants", "model:")):
            return "value_objects"
        return "entities"

    if layer_dir == "infrastructure":
        if evidence.startswith("alembic:migration"):
            return "persistence/migrations"
        if evidence.startswith("io:") and any(x in evidence for x in ("sqlalchemy", "sqlmodel", "psycopg", "psycopg2", "pymongo")):
            return "persistence"
        if evidence.startswith("io:") and any(x in evidence for x in ("redis",)):
            return "caching"
        if evidence.startswith("io:") and any(x in evidence for x in ("celery", "aio_pika", "kafka", "kombu")):
            return "messaging"
        if any(x in evidence for x in ("opentelemetry", "prometheus_client", "structlog", "sentry_sdk")):
            return "monitoring"
        if any(x in evidence for x in ("uvicorn", "gunicorn")):
            return "container"
        return "external_services/adapters"

    return ""


def normalize_ddd_layer_relpath(layer_dir: str, service_rel: str, evidence: str) -> str:
    p = service_rel.replace("\\", "/").lstrip("/")
    if not p:
        return p
    first = p.split("/", 1)[0]
    allowed = DDD_LAYER_CHILDREN.get(layer_dir, set())
    if first in allowed:
        return p
    prefix = _choose_ddd_child_prefix(layer_dir, evidence) or first
    return f"{prefix}/{p}"


def extract_module_docstring_first_line(python_source: str) -> str | None:
    try:
        tree = ast.parse(python_source)
    except SyntaxError:
        return None
    doc = ast.get_docstring(tree)
    if not doc:
        return None
    first = doc.strip().splitlines()[0].strip()
    return first or None


def parse_top_level_defs(python_source: str) -> tuple[list[str], list[str]]:
    try:
        tree = ast.parse(python_source)
    except SyntaxError:
        return ([], [])

    class_names: list[str] = []
    func_names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            class_names.append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_names.append(node.name)
    return class_names, func_names


def parse_import_roots(python_source: str) -> set[str]:
    roots: set[str] = set()
    try:
        tree = ast.parse(python_source)
    except SyntaxError:
        return roots

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                roots.add(node.module.split(".", 1)[0])
    return roots


def parse_class_base_names(python_source: str) -> set[str]:
    try:
        tree = ast.parse(python_source)
    except SyntaxError:
        return set()

    base_names: set[str] = set()

    def _name_from_expr(expr: ast.expr) -> str | None:
        if isinstance(expr, ast.Name):
            return expr.id
        if isinstance(expr, ast.Attribute):
            return expr.attr
        if isinstance(expr, ast.Subscript):
            return _name_from_expr(expr.value)
        if isinstance(expr, ast.Call):
            return _name_from_expr(expr.func)
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                name = _name_from_expr(base)
                if name:
                    base_names.add(name)
    return base_names


def parse_class_names(python_source: str) -> list[str]:
    try:
        tree = ast.parse(python_source)
    except SyntaxError:
        return []
    return [node.name for node in tree.body if isinstance(node, ast.ClassDef)]


def python_module_stats(python_source: str) -> dict[str, int]:
    try:
        tree = ast.parse(python_source)
    except SyntaxError:
        return {"statements": 0, "imports": 0, "assignments": 0, "classes": 0, "functions": 0}

    imports = 0
    assignments = 0
    classes = 0
    functions = 0

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports += 1
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            assignments += 1
        elif isinstance(node, ast.ClassDef):
            classes += 1
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions += 1
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            # docstring
            continue
        else:
            # Other statements (if/try/etc.)
            pass

    statements = len([n for n in tree.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str))])
    return {
        "statements": statements,
        "imports": imports,
        "assignments": assignments,
        "classes": classes,
        "functions": functions,
    }


def module_is_effectively_empty(python_source: str) -> bool:
    stats = python_module_stats(python_source)
    return stats["statements"] == 0


def module_looks_like_constants(python_source: str) -> bool:
    stats = python_module_stats(python_source)
    # imports + assignments only (no defs) is typically constants/types glue
    return stats["classes"] == 0 and stats["functions"] == 0 and stats["assignments"] > 0


def module_defines_exception_types(python_source: str) -> bool:
    try:
        tree = ast.parse(python_source)
    except SyntaxError:
        return False
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            if isinstance(base, ast.Name) and base.id in {"Exception", "BaseException"}:
                return True
            if isinstance(base, ast.Name) and base.id.endswith(("Error", "Exception")):
                return True
            if isinstance(base, ast.Attribute) and base.attr.endswith(("Error", "Exception")):
                return True
    return False


def module_uses_filesystem_io(python_source: str) -> bool:
    """Detect direct filesystem I/O (open/read/write) in module."""
    try:
        tree = ast.parse(python_source)
    except SyntaxError:
        return False

    io_method_names = {
        "open",
        "read_text",
        "read_bytes",
        "write_text",
        "write_bytes",
        "mkdir",
        "unlink",
        "rename",
        "replace",
        "rmdir",
        "exists",
        "stat",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "open":
                return True
            if isinstance(func, ast.Attribute) and func.attr in io_method_names:
                return True
    return False


def module_uses_dynamic_imports(python_source: str, import_roots: set[str]) -> bool:
    """Detect importlib-based lazy loading / plugin discovery patterns."""
    if "importlib" in import_roots or "pkgutil" in import_roots:
        if re.search(r"\bimport_module\s*\(", python_source) or re.search(r"\bimportlib\.\w+\s*\(", python_source):
            return True
    # PEP 562 lazy attribute access is commonly used for optional deps and plugin loading.
    if re.search(r"^\s*def\s+__getattr__\s*\(", python_source, flags=re.M):
        return True
    return False


def module_has_optional_dependency_guards(python_source: str) -> bool:
    # Try/except ImportError blocks and install-hint messages are typical optional dependency boundaries.
    if "ImportError" not in python_source:
        return False
    if re.search(r"except\s+ImportError\b", python_source):
        return True
    return False


def looks_like_alembic_migration(python_source: str, import_roots: set[str]) -> bool:
    if "alembic" not in import_roots:
        return False
    if not re.search(r"^\s*revision\s*=\s*['\"]", python_source, flags=re.M):
        return False
    if not re.search(r"^\s*down_revision\s*=\s*['\"]", python_source, flags=re.M):
        return False
    if not re.search(r"^\s*def\s+upgrade\s*\(", python_source, flags=re.M):
        return False
    if not re.search(r"^\s*def\s+downgrade\s*\(", python_source, flags=re.M):
        return False
    return True


def looks_like_fastapi_inbound_adapter(python_source: str, import_roots: set[str]) -> bool:
    """Detect *inbound* HTTP adapters (routers/apps/middleware), not just incidental FastAPI imports."""
    if "fastapi" not in import_roots and "starlette" not in import_roots:
        return False

    # FastAPI app/router construction
    if re.search(r"\b(FastAPI|APIRouter)\s*\(", python_source):
        return True

    # Route decorators like @router.get("/path")
    if re.search(r"@\w+\.(get|post|put|delete|patch|options|head)\b", python_source):
        return True

    # Middleware wiring
    if re.search(r"\badd_middleware\s*\(", python_source):
        return True
    if re.search(r"\b(BaseHTTPMiddleware|CORSMiddleware)\b", python_source):
        return True

    return False


def looks_like_cli_adapter(python_source: str, import_roots: set[str]) -> bool:
    if "typer" in import_roots or "click" in import_roots:
        return True
    if re.search(r"\b(typer\.Typer|click\.group|click\.command)\b", python_source):
        return True
    return False


def looks_like_service_port(python_source: str) -> bool:
    # Heuristic: file contains Protocol/ABC definitions, but no obvious concrete implementations.
    if "Protocol" not in python_source and "ABC" not in python_source:
        return False
    if re.search(r"\bclass\s+\w+\s*\(.*\bProtocol\b.*\)\s*:", python_source):
        return True
    if re.search(r"\bclass\s+\w+\s*\(.*\bABC\b.*\)\s*:", python_source) and re.search(
        r"\babstractmethod\b", python_source
    ):
        return True
    return False


def looks_like_infrastructure_io(import_roots: set[str]) -> tuple[bool, list[str]]:
    infra_roots = [
        "sqlalchemy",
        "sqlmodel",
        "alembic",
        "celery",
        "opentelemetry",
        "kubernetes",
        "boto3",
        "botocore",
        "redis",
        "httpx",
        "requests",
        "aiohttp",
        # LLM / vector DB / provider SDKs (treat as infrastructure integrations in DDD migration)
        "langchain",
        "langchain_core",
        "langchain_community",
        "altk",
        "crewai",
        "openai",
        "anthropic",
        "litellm",
        "groq",
        "cohere",
        "mistralai",
        "vertexai",
        "google",
        "googleapiclient",
        "azure",
        "boto3",
        "qdrant_client",
        "pinecone",
        "weaviate",
        "chromadb",
        "elasticsearch",
        "opensearchpy",
        "neo4j",
        "pgvector",
        "pymongo",
        "psycopg",
        "psycopg2",
        "pypdf",
        "docx",
        "docx2txt",
        "PIL",
        "cv2",
        "torch",
        "transformers",
        "tiktoken",
        # Misc provider/community packages that commonly appear in components
        "astrapy",
        "faiss",
        "pandas",
        "numpy",
    ]
    hits = [r for r in infra_roots if r in import_roots]
    # Catch provider packages like langchain_openai, langchain_google_genai, etc.
    hits += [r for r in import_roots if r.startswith("langchain") and r not in hits]
    return (len(hits) > 0), hits


def third_party_imports(import_roots: set[str]) -> list[str]:
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    internal = {"lfx", "langflow"}
    allowed_general = {
        # Common non-infrastructure libraries that are acceptable inside core layers.
        "pydantic",
        "typing_extensions",
        "orjson",
        "yaml",
        "chardet",
        "defusedxml",
        "packaging",
        "dotenv",
    }
    third_party = [
        r for r in import_roots if r not in stdlib and r not in internal and r not in allowed_general and r != "__future__"
    ]
    return sorted(set(third_party))


def infer_layer_from_content(source_path: str, content: bytes) -> tuple[str, str, str]:
    """
    Returns (layer, confidence, evidence)
    layer in {Domain, Application, Interface, Infrastructure}
    """
    p = source_path.replace("\\", "/")
    basename = p.rsplit("/", 1)[-1]
    suffix = Path(basename).suffix.lower()

    # Non-code artifacts (config/docs/assets) -> Infrastructure (they live at service root or infra/config).
    if basename in {"Dockerfile", "Dockerfile.dev", "Makefile", "pyproject.toml", "uv.lock", "alembic.ini"}:
        return "Infrastructure", "High", f"artifact:{basename}"
    if suffix in {".md", ".toml", ".ini", ".lock", ".example", ".json", ".yaml", ".yml", ".svg", ".typed", ".conf"}:
        return "Infrastructure", "High", f"artifact:{suffix or basename}"
    if suffix not in {".py"}:
        return "Infrastructure", "High", f"artifact:{suffix or basename}"

    # Python: decode and analyze content
    text = content.decode("utf-8", errors="replace")
    import_roots = parse_import_roots(text)

    if module_is_effectively_empty(text):
        return "Domain", "Low", "empty"

    if looks_like_fastapi_inbound_adapter(text, import_roots):
        return "Interface", "High", "fastapi:inbound"

    if looks_like_cli_adapter(text, import_roots):
        evidence = "typer" if "typer" in import_roots else ("click" if "click" in import_roots else "cli-adapter")
        return "Interface", "High", evidence

    if looks_like_alembic_migration(text, import_roots):
        return "Infrastructure", "High", "alembic:migration"

    infra_io, infra_hits = looks_like_infrastructure_io(import_roots)
    if infra_io:
        return "Infrastructure", "High" if any(x in infra_hits for x in ("sqlalchemy", "sqlmodel", "celery")) else "Medium", (
            "io:" + ",".join(sorted(infra_hits)[:3])
        )

    third_party = third_party_imports(import_roots)
    if third_party:
        return "Infrastructure", "Medium", "io:third-party:" + ",".join(third_party[:3])

    if module_uses_filesystem_io(text):
        return "Infrastructure", "Medium", "io:filesystem"

    if module_uses_dynamic_imports(text, import_roots):
        return "Infrastructure", "Medium", "infra:dynamic-import"

    if module_has_optional_dependency_guards(text):
        return "Infrastructure", "Medium", "infra:optional-deps"

    # Exception types are typically domain-level error concepts.
    if module_defines_exception_types(text):
        return "Domain", "Medium", "domain:exceptions"

    if module_looks_like_constants(text):
        return "Domain", "Medium", "domain:constants"

    if looks_like_service_port(text):
        # Service-style abstractions are part of infrastructure in this migration.
        port_class_names = parse_class_names(text)
        if any(name == "Service" or name.endswith("Service") for name in port_class_names):
            return "Infrastructure", "Medium", "infra:service-port"
        # If it is a Service+ABC/Protocol, treat as infrastructure abstraction.
        if "lfx" in import_roots or "langflow" in import_roots:
            if re.search(r"\bfrom\s+(lfx|langflow)\.services\.", text):
                return "Infrastructure", "Medium", "infra:service-abc"
        return "Application", "Medium", "port:Protocol/ABC"

    base_names = parse_class_base_names(text)
    class_names = parse_class_names(text)

    if "NamedTuple" in base_names or "TypedDict" in base_names:
        return "Domain", "Medium", "model:typing"

    if any(name == "Service" or name.endswith("Service") for name in class_names):
        return "Infrastructure", "Medium", "class:Service"

    if "Service" in base_names or any(b.endswith("Service") for b in base_names):
        return "Infrastructure", "Medium", "class:Service"

    if any(name.endswith(("Client", "Adapter", "Provider", "Repository")) for name in class_names):
        return "Infrastructure", "Medium", "class:adapter"

    if any(name.endswith(("Router", "Controller", "Middleware")) for name in class_names):
        return "Interface", "Medium", "class:http-adapter"

    if any("Component" in b for b in base_names):
        return "Domain", "Medium", "class:Component"
    if "BaseModel" in base_names:
        return "Domain", "Medium", "model:BaseModel"
    if "Enum" in base_names:
        return "Domain", "Medium", "model:Enum"
    if re.search(r"^\s*@dataclass\b", text, flags=re.M):
        return "Domain", "Medium", "model:dataclass"

    if any(name in {"Graph", "Edge", "Vertex"} for name in class_names):
        return "Application", "Medium", "core:graph-runtime"

    # Fallback: try to separate pure core logic from orchestration using light signals.
    # If module contains many async functions / task orchestration terms, bias to Application.
    if re.search(r"\basync\s+def\b", text) and re.search(r"\b(run|execute|load|serve|startup|shutdown)\b", text):
        return "Application", "Medium", "orchestration:async"

    # Default to Domain (core logic) with medium confidence once we know it's non-empty and non-I/O.
    # Keep truly empty modules as Low above.
    return "Domain", "Medium", "core:logic"


def summarize_non_python(path: str, content: bytes) -> str:
    basename = path.replace("\\", "/").rsplit("/", 1)[-1]
    suffix = Path(basename).suffix.lower()

    if basename in {"Dockerfile", "Dockerfile.dev"}:
        return "Container image build definition."
    if basename == "Makefile":
        return "Build and automation targets."
    if basename == "pyproject.toml":
        return "Python project configuration."
    if basename in {"uv.lock"}:
        return "Dependency lockfile."
    if basename == "alembic.ini":
        return "Alembic migration configuration."

    if suffix == ".md":
        text = content.decode("utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("#"):
                return line.lstrip("#").strip() or "Markdown documentation."
        return "Markdown documentation."
    if suffix in {".toml", ".ini", ".conf"}:
        return "Configuration file."
    if suffix in {".json"}:
        return "JSON data/config file."
    if suffix in {".svg"}:
        return "SVG asset."
    return "Non-Python artifact."


def summarize_python_module(text: str, import_roots: set[str]) -> str:
    doc_first = extract_module_docstring_first_line(text)
    if doc_first:
        return doc_first

    class_names, func_names = parse_top_level_defs(text)
    if class_names:
        shown = ", ".join(class_names[:3])
        more = "…" if len(class_names) > 3 else ""
        return f"Defines classes: {shown}{more}."
    if func_names:
        shown = ", ".join(func_names[:3])
        more = "…" if len(func_names) > 3 else ""
        return f"Defines functions: {shown}{more}."

    if import_roots:
        shown = ", ".join(sorted(import_roots)[:4])
        return f"Module with imports: {shown}."
    return "Python module."


def infer_target_path(context: str, layer: str, service_rel: str, *, evidence: str) -> str:
    service = infer_service_name(context)
    p = service_rel.replace("\\", "/")
    basename = p.rsplit("/", 1)[-1]
    suffix = Path(basename).suffix.lower()

    # Align with `ddd四层微服务目录结构-python.md`: service entrypoint lives at service root.
    if p == "main.py":
        return f"services/{service}/main.py"

    root_artifacts = {"Dockerfile", "Dockerfile.dev", "Makefile", "pyproject.toml", "uv.lock", "alembic.ini"}
    if basename in root_artifacts or suffix in {".md", ".toml", ".ini", ".lock", ".example"} and "/" not in p:
        return f"services/{service}/{p}"

    layer_dir = {
        "Interface": "presentation",
        "Application": "application",
        "Domain": "domain",
        "Infrastructure": "infrastructure",
    }[layer]
    normalized = normalize_ddd_layer_relpath(layer_dir, p, evidence)
    return f"services/{service}/{layer_dir}/{normalized}"


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    system_analysis_path = repo_root / "系统分析.md"
    agent_path = repo_root / "AGENT.md"
    ddd_ref_path = repo_root / "ddd四层微服务目录结构-python.md"
    output_path = repo_root / "docs/migration/mapping-matrix.md"

    entries, analysis_generated_at = parse_system_analysis(system_analysis_path)

    rows: list[dict[str, str]] = []
    missing_files: list[str] = []
    python_ast_failures: list[str] = []
    empty_python_modules: list[str] = []
    python_files = 0
    python_ast_ok = 0
    for entry in entries:
        src = repo_root / entry.source_path
        try:
            content = src.read_bytes()
        except OSError:
            content = b""
            missing_files.append(entry.source_path)

        context = infer_context(entry.source_path)
        service_rel = compute_service_rel(entry.source_path, context)

        layer, confidence, evidence = infer_layer_from_content(entry.source_path, content)

        core_resp = ""
        if entry.source_path.endswith(".py"):
            python_files += 1
            text = content.decode("utf-8", errors="replace")
            try:
                ast.parse(text)
            except SyntaxError:
                python_ast_failures.append(entry.source_path)
            else:
                python_ast_ok += 1

            import_roots = parse_import_roots(text)
            core_resp = summarize_python_module(text, import_roots)

            if evidence == "empty":
                empty_python_modules.append(entry.source_path)
        else:
            core_resp = summarize_non_python(entry.source_path, content)

        target = infer_target_path(context, layer, service_rel, evidence=evidence)

        rows.append(
            {
                "source": entry.source_path,
                "core_resp": core_resp,
                "context": context,
                "service_rel": service_rel,
                "layer": layer,
                "confidence": confidence,
                "evidence": evidence,
                "target": target,
            }
        )

    # Post-process empty python modules: infer layer from nearby non-empty modules in the same package tree.
    # Rationale: empty __init__.py files are package markers; their responsibility is defined by the package contents.
    if empty_python_modules:
        by_source = {r["source"]: r for r in rows}
        for src_path in empty_python_modules:
            r = by_source.get(src_path)
            if not r:
                continue
            src_dir = src_path.rsplit("/", 1)[0] if "/" in src_path else ""
            if not src_dir:
                continue
            # Collect non-empty modules under the same directory tree.
            siblings = [
                other
                for other in rows
                if other["source"].startswith(src_dir + "/")
                and other["source"] != src_path
                and other.get("evidence") != "empty"
                and other.get("confidence") in {"Medium", "High"}
            ]
            if not siblings:
                continue
            from collections import Counter

            counts = Counter(o["layer"] for o in siblings)
            inferred_layer, inferred_count = counts.most_common(1)[0]
            total = sum(counts.values())
            inferred: str | None = None
            inferred_reason: str | None = None

            # Prefer a strong majority when available.
            if total and inferred_count / total >= 0.6:
                inferred = inferred_layer
                inferred_reason = f"empty:inferred-majority({dict(counts)})"
            else:
                # Evidence-weighted fallback for ties / mixed packages.
                score = Counter()
                for o in siblings:
                    ev = str(o.get("evidence") or "")
                    if o["layer"] == "Infrastructure" or ev.startswith(("io:", "infra:", "class:Service")):
                        score["Infrastructure"] += 2
                    if o["layer"] == "Application" or ev.startswith(("core:graph-runtime", "orchestration:")):
                        score["Application"] += 2
                    if o["layer"] == "Domain" or ev.startswith(("model:", "domain:")):
                        score["Domain"] += 2
                    if o["layer"] == "Interface" or ev.startswith(("fastapi:",)) or "typer" in ev or "click" in ev:
                        score["Interface"] += 2
                if score:
                    inferred, _ = score.most_common(1)[0]
                    inferred_reason = f"empty:inferred-evidence({dict(counts)},{dict(score)})"

            if inferred:
                r["layer"] = inferred
                r["confidence"] = "Medium"
                r["evidence"] = inferred_reason or "empty:inferred"
                r["target"] = infer_target_path(r["context"], inferred, r["service_rel"], evidence=r["evidence"])

    layer_order = {"Interface": 0, "Application": 1, "Domain": 2, "Infrastructure": 3}
    rows.sort(key=lambda r: (r["context"], layer_order.get(r["layer"], 99), r["source"]))

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_files = len(entries)
    non_python_files = total_files - python_files
    header_lines = [
        "# DDD 映射矩阵（基于源码内容扫描自动生成）",
        "",
        f"- 生成时间: {now}",
        f"- 输入: `{system_analysis_path.relative_to(repo_root)}`"
        + (f"（原分析生成于: {analysis_generated_at}）" if analysis_generated_at else ""),
        f"- 参考: `{agent_path.relative_to(repo_root)}`（迁移规则/分层原则）, `{ddd_ref_path.relative_to(repo_root)}`（目标目录结构）",
        f"- 覆盖统计: 总文件 {total_files}；Python {python_files}（AST 解析成功 {python_ast_ok}，失败 {len(python_ast_failures)}）；非 Python {non_python_files}；读取失败 {len(missing_files)}",
        "",
        "说明：",
        "- 本文档为迁移前的初始映射：对 `系统分析.md` 列出的文件逐个读取**真实文件内容**，从源码抽取 imports/框架用法/定义模式等特征后推断 DDD 分层。",
        "- `核心职责` 字段仅从文件内容生成（优先 docstring，其次 AST 提取的类/函数定义），不使用文件名/目录名推断职责。",
        "- 仍可能存在需要人工复核的条目（见 `映射置信度=Low`），尤其是“领域层 vs 应用层”的边界。此时请以实际业务语义与依赖方向为准。",
        "",
        "---",
        "",
        "| 原模块/包名 | 核心职责 | 对应DDD上下文 | 对应分层 | 映射置信度 | 证据（源码特征） | 建议目标路径 |",
        "|-------------|----------|---------------|----------|------------|------------------|--------------|",
    ]

    out_lines = header_lines[:]
    for r in rows:
        # Ensure no newlines in table cells
        core = r["core_resp"].replace("\n", " ").strip()
        core = core[:180] + "…" if len(core) > 180 else core
        evidence = r["evidence"].replace("\n", " ").strip()
        out_lines.append(
            f"| `{r['source']}` | {core} | {r['context']} | {r['layer']} | {r['confidence']} | {evidence} | `{r['target']}` |"
        )

    out_lines += [
        "",
        "---",
        "",
        "## 置信度判定标准（简述）",
        "- High：源码特征非常明确（HTTP/CLI 适配器、DB/迁移/外部 I/O、配置/构建/文档类文件等）。",
        "- Medium：源码特征能支持分层判断，但可能在后续重构中需要拆分职责或引入端口以满足依赖倒置。",
        "- Low：仅能基于弱特征做初判（例如缺少 docstring、缺少明显 I/O/适配器信号）；需要人工复核与依赖图辅助。",
    ]

    if missing_files or python_ast_failures:
        out_lines += [
            "",
            "## 解析告警（需要人工确认）",
        ]
        if missing_files:
            out_lines += [
                "",
                f"- 读取失败（{len(missing_files)}）：",
            ] + [f"  - `{p}`" for p in missing_files[:50]]
            if len(missing_files) > 50:
                out_lines.append(f"  - …（省略 {len(missing_files) - 50} 条）")
        if python_ast_failures:
            out_lines += [
                "",
                f"- Python AST 解析失败（{len(python_ast_failures)}）：",
            ] + [f"  - `{p}`" for p in python_ast_failures[:50]]
            if len(python_ast_failures) > 50:
                out_lines.append(f"  - …（省略 {len(python_ast_failures) - 50} 条）")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
