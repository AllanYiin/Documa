"""Documa-owned MCP/tool visibility profiles.

Profiles are a server policy, not an MCP client capability.  Every call path
must still enforce the same allow-list used for discovery.
"""

from __future__ import annotations


# The agent profile must cover the full evidence workflow in the documa-evidence
# skill: overview (block_tree/list_blocks), search, read, neighbor escalation
# (source_window/block_xref), and citation close-out.
AGENT_TOOLS = {
    "documa_parse",
    "documa_process",
    "documa_ingest",
    "documa_list_documents",
    "documa_block_tree",
    "documa_list_blocks",
    "documa_search_blocks",
    "documa_search_collection",
    "documa_read_block",
    "documa_read_blocks",
    "documa_source_window",
    "documa_block_xref",
    "documa_cite_block",
    "documa_cite_chunk",
    "documa_render_citation",
    "documa_verify_citations",
    "documa_load_skill",
    "documa_read_skill_resource",
}

ADVANCED_TOOLS = AGENT_TOOLS | {
    "documa_inspect",
    "documa_view",
    "documa_inspect_block",
    "documa_index_collection",
}

ADMIN_TOOLS = ADVANCED_TOOLS | {
    "documa_ingest_mailbox",
    "documa_export",
    "documa_benchmark",
    "documa_doctor",
    "documa_validate_ir",
    "documa_inspect_store",
    "documa_sync_skills",
    "documa_skill_status",
    "documa_inspect_skill_graph",
}

TOOL_PROFILES = {"agent": AGENT_TOOLS, "advanced": ADVANCED_TOOLS, "admin": ADMIN_TOOLS}


def allowed_tool_names(profile: str = "admin") -> set[str]:
    normalized = profile.casefold()
    if normalized not in TOOL_PROFILES:
        raise ValueError(f"Unknown Documa tool profile: {profile}")
    return set(TOOL_PROFILES[normalized])
