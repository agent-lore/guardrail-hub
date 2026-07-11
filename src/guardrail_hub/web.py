"""The FastAPI dashboard: overview, per-repo detail, docs, compare, drift.

Server-rendered Jinja2 pages in the lithos-lens house style; charts hydrate
client-side from the ``history.json`` endpoints (uPlot) and Mermaid renders
diagram fences in the browser. All repo access goes through the ``RepoStore``
cache and runs in worker threads so one slow ``git`` never blocks the loop.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from guardrail_hub import docs_render
from guardrail_hub.budget_ledger import raise_counts
from guardrail_hub.config import HubConfig, load_config
from guardrail_hub.drift import compare_repo
from guardrail_hub.history import extract, series
from guardrail_hub.hotspots import component_hotspots
from guardrail_hub.kit import kit_version
from guardrail_hub.logging_setup import configure_logging
from guardrail_hub.models import BudgetEvent, DriftReport, RepoEntry, RepoSnapshot
from guardrail_hub.store import RepoStore, StoreFactory

PACKAGE_ROOT = Path(__file__).resolve().parent
TEMPLATE_DIR = PACKAGE_ROOT / "templates"
STATIC_DIR = PACKAGE_ROOT / "static"

MAX_FILE_BYTES = 2_000_000

# Default trend keys — the kit's metrics_history DEFAULT_KEYS plus test ratio.
TREND_KEYS = [
    "graph.cross_component_edges",
    "graph.cross_component_module_edges",
    "graph.component_cycles.count",
    "graph.module_cycle_count",
    "size.modules_over_800.count",
    "size.max_module_lines",
    "size.total_sloc",
    "complexity.functions_over_10",
    "seams.cross_module_private_refs",
    "seams.tests_private_imports",
    "tests.ratio",
]

# Headline metrics for the overview cards and the compare table.
COMPARE_ROWS = [
    ("graph.cross_component_edges", "Cross-component edges"),
    ("graph.cross_component_module_edges", "Module-level cross edges"),
    ("graph.component_cycles.count", "Component cycles"),
    ("graph.module_cycle_count", "Module cycles"),
    ("size.total_sloc", "Total SLOC"),
    ("size.max_module_lines", "Largest module (lines)"),
    ("size.modules_over_800.count", "Modules > 800 lines"),
    ("complexity.functions_over_10", "Functions cx > 10"),
    ("seams.cross_module_private_refs", "Private-access refs"),
    ("domain.models", "Domain models"),
    ("tests.ratio", "Test/src ratio"),
    ("mcp.tools", "MCP tools"),
]


def _worst_level(snapshot: RepoSnapshot) -> str:
    order = ("breach", "unknown", "tight", "ok")
    levels = {b.level for b in snapshot.budgets}
    return next((level for level in order if level in levels), "unknown")


def create_app(config: HubConfig, store_factory: StoreFactory | None = None) -> FastAPI:
    store = (store_factory or RepoStore)(config)
    app = FastAPI(title="Guardrail Hub")
    templates = Jinja2Templates(directory=TEMPLATE_DIR)
    templates.env.filters["short"] = lambda sha: sha[:7]
    templates.env.globals["kit_version"] = kit_version()

    def _entry_or_404(name: str) -> RepoEntry:
        entry = store.entry(name)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"unknown repo {name!r}")
        return entry

    async def _snapshots() -> list[RepoSnapshot]:
        return list(
            await asyncio.gather(*(asyncio.to_thread(store.snapshot, e) for e in store.entries))
        )

    @app.get("/health")
    async def health() -> JSONResponse:
        snapshots = await _snapshots()
        return JSONResponse(
            {
                "ok": True,
                "repos": {
                    s.entry.name: {
                        "present": s.status.present,
                        "branch": s.status.branch,
                        "dirty": s.status.dirty,
                        "error": s.status.error,
                    }
                    for s in snapshots
                },
            }
        )

    @app.get("/", response_class=HTMLResponse)
    async def overview(request: Request) -> HTMLResponse:
        snapshots = await _snapshots()
        families: dict[str, list[RepoSnapshot]] = {}
        for snapshot in snapshots:
            families.setdefault(snapshot.entry.family, []).append(snapshot)
        return templates.TemplateResponse(
            request,
            "overview.html",
            {
                "active_view": "overview",
                "families": families,
                "extract": extract,
                "worst_level": _worst_level,
            },
        )

    @app.get("/repos/{name}", response_class=HTMLResponse)
    async def repo_detail(request: Request, name: str) -> HTMLResponse:
        entry = _entry_or_404(name)
        snapshot = await asyncio.to_thread(store.snapshot, entry)
        points = await asyncio.to_thread(store.history, entry)
        drift = await asyncio.to_thread(compare_repo, entry)
        ledger = await asyncio.to_thread(store.ledger, entry)
        coupling = await asyncio.to_thread(store.coupling, entry)
        return templates.TemplateResponse(
            request,
            "repo.html",
            {
                "active_view": "overview",
                "snapshot": snapshot,
                "history_count": len(points),
                "drift": drift,
                "trend_keys": TREND_KEYS,
                "extract": extract,
                "hotspots": component_hotspots(points)[:10],
                "coupling": coupling,
                "ledger": list(reversed(ledger))[:10],
            },
        )

    @app.get("/repos/{name}/history.json")
    async def api_history(name: str, keys: str = "") -> JSONResponse:
        entry = _entry_or_404(name)
        points = await asyncio.to_thread(store.history, entry)
        wanted = [k.strip() for k in keys.split(",") if k.strip()] or TREND_KEYS
        return JSONResponse(series(points, wanted))

    @app.get("/repos/{name}/docs/{doc_path:path}", response_class=HTMLResponse)
    async def doc_view(request: Request, name: str, doc_path: str) -> HTMLResponse:
        entry = _entry_or_404(name)
        generated = entry.root / "docs" / "generated"
        target = docs_render.contained(generated, generated / doc_path)
        if target is None or not target.is_file() or target.suffix != ".md":
            raise HTTPException(status_code=404, detail="no such generated doc")
        text = target.read_text(encoding="utf-8")
        body = docs_render.render_markdown(text, name, doc_path)
        return templates.TemplateResponse(
            request,
            "doc.html",
            {
                "active_view": "overview",
                "repo_name": name,
                "doc_path": doc_path,
                "body": body,
                "needs_mermaid": 'class="mermaid"' in body,
            },
        )

    @app.get("/repos/{name}/file/{file_path:path}", response_class=HTMLResponse)
    async def repo_file(request: Request, name: str, file_path: str) -> HTMLResponse:
        entry = _entry_or_404(name)
        target = docs_render.contained(entry.root, entry.root / file_path)
        if (
            target is None
            or not target.is_file()
            or ".git" in Path(file_path).parts
            or target.stat().st_size > MAX_FILE_BYTES
        ):
            raise HTTPException(status_code=404, detail="no such file")
        text = target.read_text(encoding="utf-8", errors="replace")
        if target.suffix == ".md":
            body = docs_render.render_markdown(text, name, f"../../{file_path}")
        else:
            body = docs_render.render_plain(text)
        return templates.TemplateResponse(
            request,
            "doc.html",
            {
                "active_view": "overview",
                "repo_name": name,
                "doc_path": file_path,
                "body": body,
                "needs_mermaid": 'class="mermaid"' in body,
            },
        )

    @app.get("/compare", response_class=HTMLResponse)
    async def compare(request: Request, key: str = "graph.cross_component_edges") -> HTMLResponse:
        snapshots = await _snapshots()
        rows = [
            (dotted, label, [extract(s.metrics, dotted) if s.metrics else None for s in snapshots])
            for dotted, label in COMPARE_ROWS
        ]
        return templates.TemplateResponse(
            request,
            "compare.html",
            {
                "active_view": "compare",
                "snapshots": snapshots,
                "rows": rows,
                "selected_key": key,
                "trend_keys": TREND_KEYS,
            },
        )

    @app.get("/compare/history.json")
    async def api_compare_history(key: str = "graph.cross_component_edges") -> JSONResponse:
        payload = {}
        for entry in store.entries:
            points = await asyncio.to_thread(store.history, entry)
            data = series(points, [key])
            payload[entry.name] = {
                "dates": data["dates"],
                "values": data["series"][key],
            }
        return JSONResponse({"key": key, "repos": payload})

    @app.get("/ledger", response_class=HTMLResponse)
    async def ledger_panel(request: Request) -> HTMLResponse:
        ledgers: list[tuple[RepoEntry, tuple[BudgetEvent, ...]]] = list(
            zip(
                store.entries,
                await asyncio.gather(*(asyncio.to_thread(store.ledger, e) for e in store.entries)),
                strict=True,
            )
        )
        events = sorted(
            ((entry, event) for entry, entry_events in ledgers for event in entry_events),
            key=lambda pair: (pair[1].date, pair[1].sha),
            reverse=True,
        )
        raises = sorted(
            (
                (entry.name, key, count)
                for entry, entry_events in ledgers
                for key, count in raise_counts(entry_events).items()
            ),
            key=lambda row: (-row[2], row[0], row[1]),
        )
        return templates.TemplateResponse(
            request,
            "ledger.html",
            {"active_view": "ledger", "events": events, "raises": raises},
        )

    @app.get("/drift", response_class=HTMLResponse)
    async def drift_panel(request: Request) -> HTMLResponse:
        reports: list[DriftReport] = list(
            await asyncio.gather(*(asyncio.to_thread(compare_repo, e) for e in store.entries))
        )
        return templates.TemplateResponse(
            request,
            "drift.html",
            {"active_view": "drift", "reports": reports},
        )

    @app.post("/refresh")
    async def refresh_all(request: Request) -> RedirectResponse:
        store.refresh()
        return RedirectResponse(request.headers.get("referer", "/"), status_code=303)

    @app.post("/repos/{name}/refresh")
    async def refresh_repo(request: Request, name: str) -> RedirectResponse:
        _entry_or_404(name)
        store.refresh(name)
        return RedirectResponse(request.headers.get("referer", f"/repos/{name}"), status_code=303)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def serve(config_path: Path | None = None, host: str | None = None, port: int | None = None) -> int:
    """Load config and run the dashboard under uvicorn (blocking)."""
    import uvicorn

    config = load_config(config_path)
    configure_logging(config.log_level)
    uvicorn.run(
        create_app(config),
        host=host or config.server.host,
        port=port or config.server.port,
        log_config=None,
    )
    return 0
