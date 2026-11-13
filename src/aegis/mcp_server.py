"""MCP server: lets Claude (Desktop, Claude Code) or any MCP client drive Aegis.

    aegis mcp            # stdio transport

Claude Desktop config (claude_desktop_config.json):
    {"mcpServers": {"aegis": {"command": "aegis", "args": ["mcp"],
                              "env": {"ANTHROPIC_API_KEY": "...", "AEGIS_DB": "/abs/path/data/aegis.db"}}}}

The approval gate still applies: an MCP client can request outreach, but a
HIGH-risk send waits for decide_approval, which is a separate, explicit call.
"""
from __future__ import annotations

import json

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # 1.x
    from mcp.server.fastmcp import FastMCP as _Server

from .config import Settings
from .services import RunOptions, Runtime

mcp = _Server("aegis")
_settings = Settings()
_rt = Runtime(_settings, RunOptions(mode="live" if _settings.has_llm() else "offline"))


def _summary(run_id: str) -> str:
    r = _rt.store.get_run(run_id)
    out = (r.get("result") or {}).get("output")
    return json.dumps({"run_id": run_id, "status": r["status"], "error": r["error"], "output": out}, default=str)[:20000]


@mcp.tool()
async def curate_variant(variant: str, phenotype: str = "") -> str:
    """Gather and verify ACMG evidence for a GRCh38 variant (CHROM-POS-REF-ALT) and return a sourced classification."""
    rid = await _rt.run("variant", {"variant": variant, "phenotype": phenotype, "use_browser": True})
    return _summary(rid)


@mcp.tool()
async def research_company(start_url: str, draft_outreach: bool = False) -> str:
    """Browse a company's website and return a brief where every fact is quoted and verified against the page."""
    rid = await _rt.run("company", {"start_url": start_url, "draft_outreach": draft_outreach})
    return _summary(rid)


@mcp.tool()
def get_run(run_id: str) -> str:
    """Status, error and output of a run."""
    return _summary(run_id)


@mcp.tool()
def pending_approvals() -> str:
    """High-risk actions waiting for a human decision."""
    return json.dumps(_rt.store.list_approvals("pending"), default=str)


@mcp.tool()
async def decide_approval(approval_id: int, approved: bool) -> str:
    """Approve or reject a pending high-risk action, then resume its run."""
    row = _rt.store.decide_approval(approval_id, approved, by="mcp-client")
    await _rt.resume(row["run_id"])
    return _summary(row["run_id"])


def main() -> None:
    mcp.run()
