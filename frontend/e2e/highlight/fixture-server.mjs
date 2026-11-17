import { createHash } from "node:crypto";
import { readFile, stat } from "node:fs/promises";
import { createServer } from "node:http";
import { fileURLToPath } from "node:url";
import path from "node:path";

const port = Number(process.env.NASA_FIXTURE_PORT ?? 4174);
const pdfPath = fileURLToPath(new URL("../../../data/pdf/mars-science-laboratory.pdf", import.meta.url));
const figurePath = fileURLToPath(new URL("../../../data/images_pymupdf/mars-science-laboratory/page_2_img_0.png", import.meta.url));
const pdf = await readFile(pdfPath);
const figure = await readFile(figurePath);
const pdfSha = createHash("sha256").update(pdf).digest("hex");
const expectedPdfSha = "2baee40744fc1c35b3ad015f8672a45710ac6bd9124a4041cfc0d7bfba4e10f6";
if (pdfSha !== expectedPdfSha) throw new Error(`NASA source PDF identity mismatch: ${pdfSha}`);

const documentId = `doc_${pdfSha}`;
const manifestHash = "b".repeat(64);
const indexFingerprint = "c".repeat(64);
const runFingerprint = "d".repeat(64);
const contextFingerprint = "e".repeat(64);
const reportSha = "f".repeat(64);
const buildId = "1234567890abcdef1234567890abcdef";
const imageReference = "1234567890abcdef1234567890abcdef";
const sample = {
  document_id: documentId,
  relative_path: "mars-science-laboratory.pdf",
  size_bytes: pdf.byteLength,
  sha256: pdfSha,
};

const evidence = {
  link: {
    label: "T1", document_id: documentId, source_path: sample.relative_path, page_number: 2,
    passage: "A rock outcrop called Link shows signs of being formed by the deposition of water.",
    record_id: "link-caption-record", truncated: false,
  },
  stream: {
    label: "T2", document_id: documentId, source_path: sample.relative_path, page_number: 2,
    passage: "The evidence for stream flow was in rounded pebbles mixed with hardened sand in conglomerate rocks at and near the landing site.",
    record_id: "stream-flow-record", truncated: false,
  },
  water: {
    label: "T3", document_id: documentId, source_path: sample.relative_path, page_number: 2,
    passage: "first drilled sample, from a rock target called “John Klein,” provided the evidence of conditions favorable for life in Mars’ early history: geological and mineralogical evidence for sustained liquid water,",
    record_id: "john-klein-record", truncated: false,
  },
  gale: {
    label: "T1", document_id: documentId, source_path: sample.relative_path, page_number: 1,
    passage: "at Gale Crater, using precision landing technology",
    record_id: "gale-crater-record", truncated: false,
  },
  power: {
    label: "T1", document_id: documentId, source_path: sample.relative_path, page_number: 3,
    passage: "At launch, the generator pro-\nvided about 110 watts of electrical power to operate the rover’s instruments, robotic arm, wheels, computers and radio.",
    record_id: "generator-power-record", truncated: false,
  },
  johnShort: {
    label: "T2", document_id: documentId, source_path: sample.relative_path, page_number: 2,
    passage: "first drilled sample, from a rock target called “John Klein,” provided the evidence of conditions favorable for life in Mars’ early history: geological and mineralogical evidence for sustained liquid water,",
    record_id: "john-klein-record", truncated: false,
  },
};

function answerFor(question) {
  if (question === "What does the rock Link tell us about water on Mars?") {
    return {
      answer: "The Link outcrop shows signs of formation by water deposition [T1]. Rounded pebbles and hardened sand indicate stream flow [T2]. The John Klein sample provides evidence of sustained liquid water [T3].",
      textEvidence: [evidence.link, evidence.stream, evidence.water],
      images: [{
        label: "I1", document_id: documentId, source_path: sample.relative_path, page_number: 2,
        caption: "Curiosity’s Mastcam view of the Link rock outcrop on Mars, with rounded pebbles and a one-centimeter scale marker.",
        record_id: "link-figure-record", image_reference: imageReference, content_type: "image/png", size_bytes: figure.byteLength,
      }],
    };
  }
  if (question === "How much power did the rover have at launch?") {
    return {
      answer: "At launch, the rover’s generator provided about 110 watts of electrical power [T1].",
      textEvidence: [evidence.power], images: [],
    };
  }
  if (question === "Which crater did it land in, and what did it drill first?") {
    return {
      answer: "Curiosity landed at Gale Crater [T1]. Its first drilled sample came from the John Klein rock target and showed evidence of sustained liquid water [T2].",
      textEvidence: [evidence.gale, evidence.johnShort], images: [],
    };
  }
  return {
    answer: "The source returned a fixture answer for this browser contract check.",
    textEvidence: [evidence.link], images: [],
  };
}

function responseFor(question) {
  const result = answerFor(question);
  return {
    query_id: createHash("sha256").update(question).digest("hex"),
    question,
    outcome: "answered",
    answer: result.answer,
    model_reported_insufficient_evidence: false,
    failure_code: null,
    model_identifier: "test-fixture-only",
    index_fingerprint: indexFingerprint,
    document_manifest_hash: manifestHash,
    run_fingerprint: runFingerprint,
    context_fingerprint: contextFingerprint,
    selected_build_id: buildId,
    selected_report_sha256: reportSha,
    selection_revision: 1,
    text_evidence: result.textEvidence,
    image_evidence: result.images,
    referenced_citations: result.textEvidence.map((item) => item.label),
    warnings: [],
  };
}

const selection = {
  selection_status: "selected",
  revision: 1,
  selection: {
    build_id: buildId,
    report_reference: `build-artifacts/${buildId}/build-report.json`,
    document_manifest_hash: manifestHash,
    index_fingerprint: indexFingerprint,
    report_sha256: reportSha,
    configuration_snapshot_hash: "a".repeat(64),
    run_fingerprint: runFingerprint,
    report_status: "completed",
    selection_revision: 1,
  },
  legacy_report_reference: null,
  reason_code: "selected",
  check_level: "report",
  store_checked: false,
};

function sendJson(response, status, value) {
  response.writeHead(status, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" });
  response.end(JSON.stringify(value));
}

const server = createServer(async (request, response) => {
  const url = new URL(request.url ?? "/", `http://127.0.0.1:${port}`);
  if (request.method === "GET" && (url.pathname === "/health" || url.pathname === "/bootstrap-api/health" || url.pathname === "/api/v1/health")) {
    sendJson(response, 200, { status: "ok", api_version: "v1" });
    return;
  }
  if (request.method === "GET" && url.pathname === "/api/v1/runtime/models") {
    sendJson(response, 200, { components: [] });
    return;
  }
  if (request.method === "GET" && url.pathname === "/api/v1/chat/models") {
    sendJson(response, 200, {
      availability: "available", configured_model: "gemma4:e2b", configured_model_available: true,
      available_models: ["gemma4:e2b"], error_code: null,
    });
    return;
  }
  if (request.method === "GET" && url.pathname === "/api/v1/index/selection") {
    sendJson(response, 200, selection);
    return;
  }
  if (request.method === "GET" && url.pathname === "/bootstrap-api/v1/bootstrap/status") {
    sendJson(response, 200, { mode: "bootstrap", status: "running", accepting_jobs: true, worker_count: 1, queue_capacity: 1 });
    return;
  }
  if (request.method === "GET" && url.pathname === "/bootstrap-api/v1/bootstrap/documents") {
    const limit = Number(url.searchParams.get("limit"));
    const offset = Number(url.searchParams.get("offset"));
    const documents = offset === 0 && limit > 0 ? [sample].slice(0, limit) : [];
    sendJson(response, 200, {
      inventory_status: "available", documents, manifest_hash: manifestHash, total: 1, limit, offset,
      complete: true, max_upload_bytes: 16 * 1024 * 1024, max_pages: 200,
    });
    return;
  }
  if (request.method === "GET" && /^\/bootstrap-api\/v1\/bootstrap\/documents\/doc_[0-9a-f]{64}\/content$/u.test(url.pathname) &&
    url.pathname !== `/bootstrap-api/v1/bootstrap/documents/${documentId}/content`) {
    sendJson(response, 404, { error: { code: "document_not_found" } });
    return;
  }
  if (request.method === "GET" && url.pathname === `/bootstrap-api/v1/bootstrap/documents/${documentId}/content`) {
    if (url.searchParams.get("manifest_hash") !== manifestHash) {
      sendJson(response, 409, { error: { code: "document_manifest_changed" } });
      return;
    }
    response.writeHead(200, {
      "Content-Type": "application/pdf",
      "Content-Length": String(pdf.byteLength),
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
      "X-Rag-Document-Id": documentId,
      "X-Rag-Document-Sha256": pdfSha,
      "X-Rag-Manifest-Hash": manifestHash,
      "X-Rag-Document-Name": encodeURIComponent(sample.relative_path),
      "X-Rag-Page-Count": "4",
    });
    response.end(pdf);
    return;
  }
  if (request.method === "POST" && url.pathname === "/api/v1/ask") {
    let body = "";
    for await (const chunk of request) {
      body += chunk;
      if (body.length > 24_000) { sendJson(response, 413, { error: { code: "request_too_large" } }); return; }
    }
    let parsed;
    try { parsed = JSON.parse(body); } catch { sendJson(response, 400, { error: { code: "request_invalid" } }); return; }
    sendJson(response, 200, responseFor(parsed.question));
    return;
  }
  if (request.method === "GET" && url.pathname === `/api/v1/ask/images/${imageReference}`) {
    response.writeHead(200, { "Content-Type": "image/png", "Content-Length": String(figure.byteLength), "Cache-Control": "no-store" });
    response.end(figure);
    return;
  }
  sendJson(response, 404, { error: { code: "fixture_route_not_found" } });
});

server.listen(port, "127.0.0.1", () => process.stdout.write(`NASA test fixture server listening on 127.0.0.1:${port}\n`));
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => server.close(() => process.exit(0)));
