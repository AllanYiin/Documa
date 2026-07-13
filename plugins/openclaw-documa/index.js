import { spawn } from "node:child_process";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

function runDocuma(config, args, signal) {
  const command = config?.documaCommand || "documa";

  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      shell: process.platform === "win32",
      stdio: ["ignore", "pipe", "pipe"],
      signal,
    });

    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.on("error", reject);
    child.on("close", (code) => {
      let parsed = null;
      try {
        parsed = stdout.trim() ? JSON.parse(stdout) : null;
      } catch (error) {
        reject(new Error(`documa returned non-JSON output: ${error.message}\n${stdout}`));
        return;
      }

      if (code !== 0) {
        reject(new Error(JSON.stringify({ status: "error", exitCode: code, stderr, result: parsed }, null, 2)));
        return;
      }

      resolve(parsed ?? { status: "ok" });
    });
  });
}

function asToolResult(payload) {
  return {
    content: [
      {
        type: "text",
        text: JSON.stringify(payload, null, 2),
      },
    ],
    structuredContent: payload,
  };
}

function optionalString(value) {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function optionalNumber(value) {
  return Number.isFinite(value) ? String(value) : undefined;
}

export default definePluginEntry({
  id: "documa",
  name: "Documa",
  description: "Adds Documa document-understanding tools to OpenClaw.",
  register(api) {
    api.registerTool({
      name: "documa_process",
      description: "Parse a source document into Documa IR and optional agent-ready exports.",
      parameters: {
        type: "object",
        additionalProperties: false,
        properties: {
          source: { type: "string" },
          out: { type: "string" },
          lang: { type: "string", default: "auto" },
          max_chars: { type: "number", default: 1200 },
          export_formats: {
            type: "array",
            items: { type: "string", enum: ["json", "markdown", "rag-json", "block-json"] },
          },
        },
        required: ["source"],
      },
      async execute(_id, params, context) {
        const args = ["process", params.source];
        const out = optionalString(params.out);
        const lang = optionalString(params.lang);
        const maxChars = optionalNumber(params.max_chars);
        if (out) args.push("--out", out);
        if (lang) args.push("--lang", lang);
        if (maxChars) args.push("--max-chars", maxChars);
        for (const format of params.export_formats || []) {
          args.push("--export-format", format);
        }
        return asToolResult(await runDocuma(context?.config, args, context?.signal));
      },
    });

    api.registerTool({
      name: "documa_search_blocks",
      description: "Search Documa document blocks without expanding every block body.",
      parameters: {
        type: "object",
        additionalProperties: false,
        properties: {
          ir_path: { type: "string" },
          query: { type: "string" },
          limit: { type: "number", default: 10 },
          offset: { type: "number", default: 0 },
          verbosity: { type: "string", enum: ["compact", "standard", "debug"], default: "compact" },
          no_body: { type: "boolean", default: false },
          max_response_tokens: { type: "number" },
        },
        required: ["ir_path", "query"],
      },
      async execute(_id, params, context) {
        const args = [
          "search-blocks",
          params.ir_path,
          "--query",
          params.query,
          "--limit",
          String(params.limit ?? 10),
          "--verbosity",
          params.verbosity || "compact",
        ];
        const offset = optionalNumber(params.offset);
        const maxResponseTokens = optionalNumber(params.max_response_tokens);
        if (offset && params.offset > 0) args.push("--offset", offset);
        if (maxResponseTokens) args.push("--max-response-tokens", maxResponseTokens);
        if (params.no_body) args.push("--no-body");
        return asToolResult(await runDocuma(context?.config, args, context?.signal));
      },
    });

    api.registerTool({
      name: "documa_read_block",
      description: "Read a selected Documa block body, optionally with descendants.",
      parameters: {
        type: "object",
        additionalProperties: false,
        properties: {
          ir_path: { type: "string" },
          block_id: { type: "string" },
          include_children: { type: "boolean", default: false },
          max_chars: { type: "number" },
          max_tokens: { type: "number" },
        },
        required: ["ir_path", "block_id"],
      },
      async execute(_id, params, context) {
        const args = ["block", params.ir_path, "--id", params.block_id, "--read"];
        const maxChars = optionalNumber(params.max_chars);
        const maxTokens = optionalNumber(params.max_tokens);
        if (params.include_children) args.push("--include-children");
        if (maxChars) args.push("--max-chars", maxChars);
        if (maxTokens) args.push("--max-tokens", maxTokens);
        return asToolResult(await runDocuma(context?.config, args, context?.signal));
      },
    });

    api.registerTool({
      name: "documa_ingest",
      description: "Ingest a document into the local store; returns a stable doc- id and keeps the collection index fresh.",
      parameters: {
        type: "object",
        additionalProperties: false,
        properties: {
          source: { type: "string" },
          store_dir: { type: "string", default: ".documa" },
          lang: { type: "string", default: "auto" },
          max_chars: { type: "number", default: 1200 },
          ocr: { type: "boolean", default: false },
        },
        required: ["source"],
      },
      async execute(_id, params, context) {
        const args = ["ingest", params.source, "--store-dir", params.store_dir || ".documa"];
        const lang = optionalString(params.lang);
        const maxChars = optionalNumber(params.max_chars);
        if (lang) args.push("--lang", lang);
        if (maxChars) args.push("--max-chars", maxChars);
        if (params.ocr) args.push("--ocr");
        return asToolResult(await runDocuma(context?.config, args, context?.signal));
      },
    });

    api.registerTool({
      name: "documa_list_documents",
      description: "List registry documents so collection read_refs can be resolved to doc- ids.",
      parameters: {
        type: "object",
        additionalProperties: false,
        properties: {
          store_dir: { type: "string", default: ".documa" },
        },
      },
      async execute(_id, params, context) {
        const args = ["list-documents", "--store-dir", params.store_dir || ".documa"];
        return asToolResult(await runDocuma(context?.config, args, context?.signal));
      },
    });

    api.registerTool({
      name: "documa_index_collection",
      description: "Rebuild the local collection search index (repair path; ingest maintains it incrementally).",
      parameters: {
        type: "object",
        additionalProperties: false,
        properties: {
          store_dir: { type: "string", default: ".documa" },
          collection_id: { type: "string", default: "default" },
        },
      },
      async execute(_id, params, context) {
        const args = ["index-collection", "--store-dir", params.store_dir || ".documa"];
        const collectionId = optionalString(params.collection_id);
        if (collectionId) args.push("--collection-id", collectionId);
        return asToolResult(await runDocuma(context?.config, args, context?.signal));
      },
    });

    api.registerTool({
      name: "documa_search_collection",
      description: "Search all active store documents; group_by_document returns per-document rollups for breadth questions.",
      parameters: {
        type: "object",
        additionalProperties: false,
        properties: {
          query: { type: "string" },
          store_dir: { type: "string", default: ".documa" },
          collection_id: { type: "string", default: "default" },
          limit: { type: "number", default: 20 },
          offset: { type: "number", default: 0 },
          per_document_limit: { type: "number" },
          document_ids: { type: "array", items: { type: "string" } },
          group_by_document: { type: "boolean", default: false },
          max_response_tokens: { type: "number" },
        },
        required: ["query"],
      },
      async execute(_id, params, context) {
        const args = [
          "search-collection",
          "--store-dir",
          params.store_dir || ".documa",
          "--query",
          params.query,
          "--limit",
          String(params.limit ?? 20),
        ];
        const collectionId = optionalString(params.collection_id);
        const offset = optionalNumber(params.offset);
        const perDocumentLimit = optionalNumber(params.per_document_limit);
        const maxResponseTokens = optionalNumber(params.max_response_tokens);
        if (collectionId) args.push("--collection-id", collectionId);
        if (offset && params.offset > 0) args.push("--offset", offset);
        if (perDocumentLimit) args.push("--per-document-limit", perDocumentLimit);
        for (const documentId of params.document_ids || []) {
          if (typeof documentId === "string" && documentId.length > 0) {
            args.push("--document-id", documentId);
          }
        }
        if (params.group_by_document) args.push("--group-by-document");
        if (maxResponseTokens) args.push("--max-response-tokens", maxResponseTokens);
        return asToolResult(await runDocuma(context?.config, args, context?.signal));
      },
    });

    api.registerTool({
      name: "documa_doctor",
      description: "Run Documa environment diagnostics.",
      parameters: {
        type: "object",
        additionalProperties: false,
        properties: {
          project_root: { type: "string", default: "." },
          include_benchmark: { type: "boolean", default: true },
        },
      },
      async execute(_id, params, context) {
        const args = ["doctor", "--project-root", params.project_root || "."];
        if (params.include_benchmark === false) args.push("--no-benchmark");
        return asToolResult(await runDocuma(context?.config, args, context?.signal));
      },
    });
  },
});

