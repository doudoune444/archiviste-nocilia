// AC: CHAT-002 — SSE stream consumer
//
// AC-1: consumer turns a ReadableStream<Uint8Array> into an ordered async-iterable
//       of typed chunks: meta → token* → done.
// AC-2: events split across chunk boundaries (partial lines) are re-assembled correctly.
// AC-3: a terminal "error" SSE event yields a StreamError chunk (distinct from done).
// AC-4: conversation_id is exposed from the meta chunk so a later history slice can use it.
// AC-5: the sequence of chunk types matches the SSE wire grammar exactly.

import { describe, it, expect } from "vitest";
import { consumeSseStream } from "@/lib/sse-stream";
import type { SseChunk } from "@/lib/sse-stream";

/** Encodes a string to Uint8Array (UTF-8). */
function enc(text: string): Uint8Array {
  return new TextEncoder().encode(text);
}

/**
 * Builds a ReadableStream<Uint8Array> from an array of byte chunks.
 * Each element is a separate read() result, allowing tests to simulate
 * events split across chunk boundaries.
 */
function makeStream(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  let index = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      if (index < chunks.length) {
        controller.enqueue(chunks[index++]);
      } else {
        controller.close();
      }
    },
  });
}

/** Collects all chunks from the async-iterable into an array. */
async function collectChunks(
  stream: ReadableStream<Uint8Array>
): Promise<SseChunk[]> {
  const result: SseChunk[] = [];
  for await (const chunk of consumeSseStream(stream)) {
    result.push(chunk);
  }
  return result;
}

// Wire-format helpers — mirror the Python _sse_event() helper in stream_router.py.
const META_EVENT =
  'event: meta\ndata: {"mode":"canon","conversation_id":"conv-abc","request_id":"req-xyz"}\n\n';

const TOKEN_A_EVENT = 'event: token\ndata: {"text":"Bonjour"}\n\n';
const TOKEN_B_EVENT = 'event: token\ndata: {"text":" monde"}\n\n';

const DONE_EVENT =
  'event: done\ndata: {"citations":[],"usage":{"prompt_tokens":10,"completion_tokens":5},"retrieve_ms":42,"llm_ms":200}\n\n';

const ERROR_EVENT = 'event: error\ndata: {"error":"llm_timeout"}\n\n';

describe("consumeSseStream()", () => {
  // AC-1 + AC-5: meta → token* → done in a single chunk
  it("yields meta, tokens, then done from a well-formed single-chunk stream", async () => {
    const raw = META_EVENT + TOKEN_A_EVENT + TOKEN_B_EVENT + DONE_EVENT;
    const stream = makeStream([enc(raw)]);
    const chunks = await collectChunks(stream);

    expect(chunks).toHaveLength(4);
    expect(chunks[0]?.kind).toBe("meta");
    expect(chunks[1]?.kind).toBe("token");
    expect(chunks[2]?.kind).toBe("token");
    expect(chunks[3]?.kind).toBe("done");
  });

  // AC-4: conversation_id must be exposed from the meta chunk
  it("exposes conversation_id from the meta chunk", async () => {
    const raw = META_EVENT + DONE_EVENT;
    const stream = makeStream([enc(raw)]);
    const chunks = await collectChunks(stream);

    const meta = chunks[0];
    expect(meta?.kind).toBe("meta");
    if (meta?.kind === "meta") {
      expect(meta.conversation_id).toBe("conv-abc");
      expect(meta.mode).toBe("canon");
      expect(meta.request_id).toBe("req-xyz");
    }
  });

  // AC-1: token chunk carries the text field
  it("carries the token text in each token chunk", async () => {
    const raw = META_EVENT + TOKEN_A_EVENT + DONE_EVENT;
    const stream = makeStream([enc(raw)]);
    const chunks = await collectChunks(stream);

    const token = chunks[1];
    expect(token?.kind).toBe("token");
    if (token?.kind === "token") {
      expect(token.text).toBe("Bonjour");
    }
  });

  // AC-1: done chunk carries citations, usage, retrieve_ms, llm_ms
  it("carries citations and usage in the done chunk", async () => {
    const raw = META_EVENT + DONE_EVENT;
    const stream = makeStream([enc(raw)]);
    const chunks = await collectChunks(stream);

    const done = chunks[1];
    expect(done?.kind).toBe("done");
    if (done?.kind === "done") {
      expect(done.citations).toEqual([]);
      expect(done.usage).toBeDefined();
      expect(done.retrieve_ms).toBe(42);
      expect(done.llm_ms).toBe(200);
    }
  });

  // AC-3: error event yields a StreamError chunk (distinct from done)
  it("yields a stream-error chunk on an SSE error event", async () => {
    const raw = META_EVENT + ERROR_EVENT;
    const stream = makeStream([enc(raw)]);
    const chunks = await collectChunks(stream);

    expect(chunks).toHaveLength(2);
    const errorChunk = chunks[1];
    expect(errorChunk?.kind).toBe("stream-error");
    if (errorChunk?.kind === "stream-error") {
      expect(errorChunk.error).toBe("llm_timeout");
    }
  });

  // AC-2: events split across chunk boundaries are reassembled correctly
  it("reassembles an event split mid-line across two chunks", async () => {
    // Split the token event in the middle of the data line
    const full = META_EVENT + TOKEN_A_EVENT + DONE_EVENT;
    const splitPoint = META_EVENT.length + 'event: token\ndata: {"te'.length;
    const chunkA = enc(full.slice(0, splitPoint));
    const chunkB = enc(full.slice(splitPoint));

    const stream = makeStream([chunkA, chunkB]);
    const chunks = await collectChunks(stream);

    expect(chunks).toHaveLength(3);
    expect(chunks[0]?.kind).toBe("meta");
    const tokenChunk = chunks[1];
    expect(tokenChunk?.kind).toBe("token");
    if (tokenChunk?.kind === "token") {
      expect(tokenChunk.text).toBe("Bonjour");
    }
    expect(chunks[2]?.kind).toBe("done");
  });

  // AC-2: event boundary split — the \n\n separator itself is split across chunks
  it("reassembles an event whose double-newline terminator is split across chunks", async () => {
    // Split right before the second \n of the \n\n event terminator
    const full = META_EVENT + DONE_EVENT;
    const splitPoint = META_EVENT.length - 1; // one \n into the \n\n
    const chunkA = enc(full.slice(0, splitPoint));
    const chunkB = enc(full.slice(splitPoint));

    const stream = makeStream([chunkA, chunkB]);
    const chunks = await collectChunks(stream);

    expect(chunks[0]?.kind).toBe("meta");
    expect(chunks[1]?.kind).toBe("done");
  });

  // AC-2: three separate chunks — one per raw SSE line
  it("reassembles an event delivered one line per chunk", async () => {
    const stream = makeStream([
      enc("event: meta\n"),
      enc('data: {"mode":"canon","conversation_id":"c1","request_id":"r1"}\n'),
      enc("\n"),
      enc("event: done\n"),
      enc('data: {"citations":[],"usage":{},"retrieve_ms":0,"llm_ms":0}\n'),
      enc("\n"),
    ]);

    const chunks = await collectChunks(stream);
    expect(chunks[0]?.kind).toBe("meta");
    expect(chunks[1]?.kind).toBe("done");
  });

  // AC-5: multiple tokens in correct order
  it("yields tokens in the order they were emitted", async () => {
    const raw = META_EVENT + TOKEN_A_EVENT + TOKEN_B_EVENT + DONE_EVENT;
    const stream = makeStream([enc(raw)]);
    const chunks = await collectChunks(stream);

    const tokens = chunks.filter((c) => c.kind === "token");
    expect(tokens).toHaveLength(2);
    if (tokens[0]?.kind === "token" && tokens[1]?.kind === "token") {
      expect(tokens[0].text).toBe("Bonjour");
      expect(tokens[1].text).toBe(" monde");
    }
  });
});
