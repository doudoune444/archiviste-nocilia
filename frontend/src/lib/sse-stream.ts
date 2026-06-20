/**
 * sse-stream — client-side SSE byte-stream consumer (CHAT-002).
 *
 * Turns a ReadableStream<Uint8Array> (response.body from POST /api/v1/chat/stream)
 * into an ordered async-iterable of typed, discriminated-union chunks.
 *
 * Wire format (workers emit, gateway relays VERBATIM):
 *   event: <name>\ndata: <json>\n\n
 *
 * Event types:
 *   meta  → { mode, conversation_id, request_id }
 *   token → { text }
 *   done  → { citations, usage, retrieve_ms, llm_ms }
 *   error → { error: <code> }
 *
 * Invariant: partial events are buffered across chunk boundaries so no
 * data is lost when a single ReadableStream read() splits an event mid-line.
 */

// ---------------------------------------------------------------------------
// Typed chunks — discriminated union
// ---------------------------------------------------------------------------

export interface MetaChunk {
  kind: "meta";
  mode: string;
  conversation_id: string;
  request_id: string;
}

export interface TokenChunk {
  kind: "token";
  text: string;
}

export interface DoneChunk {
  kind: "done";
  citations: unknown[];
  usage: Record<string, unknown>;
  retrieve_ms: number;
  llm_ms: number;
}

export interface StreamErrorChunk {
  kind: "stream-error";
  error: string;
}

export type SseChunk = MetaChunk | TokenChunk | DoneChunk | StreamErrorChunk;

// ---------------------------------------------------------------------------
// SSE event buffer — accumulates lines across chunk boundaries
// ---------------------------------------------------------------------------

interface SseEvent {
  event: string;
  data: string;
}

/**
 * Parses a complete SSE event block (the text between two \n\n separators)
 * into a structured { event, data } pair. Returns null for empty blocks.
 */
function parseSseEvent(block: string): SseEvent | null {
  let eventName = "";
  let dataLine = "";

  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) {
      eventName = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLine = line.slice("data:".length).trim();
    }
  }

  if (!eventName || !dataLine) return null;
  return { event: eventName, data: dataLine };
}

/**
 * Converts a parsed SSE event into a typed SseChunk.
 * Returns null if the event name is unrecognised or JSON is malformed.
 */
function toChunk(sseEvent: SseEvent): SseChunk | null {
  let payload: unknown;
  try {
    payload = JSON.parse(sseEvent.data) as unknown;
  } catch {
    return null;
  }

  if (typeof payload !== "object" || payload === null) return null;

  switch (sseEvent.event) {
    case "meta": {
      const p = payload as Record<string, unknown>;
      return {
        kind: "meta",
        mode: String(p["mode"] ?? ""),
        conversation_id: String(p["conversation_id"] ?? ""),
        request_id: String(p["request_id"] ?? ""),
      };
    }
    case "token": {
      const p = payload as Record<string, unknown>;
      return { kind: "token", text: String(p["text"] ?? "") };
    }
    case "done": {
      const p = payload as Record<string, unknown>;
      return {
        kind: "done",
        citations: Array.isArray(p["citations"]) ? p["citations"] : [],
        usage:
          typeof p["usage"] === "object" && p["usage"] !== null
            ? (p["usage"] as Record<string, unknown>)
            : {},
        retrieve_ms: typeof p["retrieve_ms"] === "number" ? p["retrieve_ms"] : 0,
        llm_ms: typeof p["llm_ms"] === "number" ? p["llm_ms"] : 0,
      };
    }
    case "error": {
      const p = payload as Record<string, unknown>;
      return { kind: "stream-error", error: String(p["error"] ?? "unknown") };
    }
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// Public async-iterable consumer
// ---------------------------------------------------------------------------

/**
 * Consumes a ReadableStream<Uint8Array> of raw SSE bytes and yields typed
 * SseChunk values in the order they arrive.
 *
 * Handles partial events across chunk boundaries by buffering the raw text
 * and splitting on the \n\n SSE event terminator only when complete.
 *
 * AC-4: the MetaChunk (always first) carries conversation_id so the caller
 * does not need to parse the stream separately to extract it.
 */
export async function* consumeSseStream(
  stream: ReadableStream<Uint8Array>
): AsyncIterable<SseChunk> {
  const decoder = new TextDecoder("utf-8");
  const reader = stream.getReader();
  // Buffer carries unterminated text from the previous read().
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      // Decode incrementally — preserves multi-byte UTF-8 sequences split
      // across chunk boundaries (TextDecoder stream: true mode).
      buffer += decoder.decode(value, { stream: true });

      // Split on the double-newline SSE event terminator.
      // We keep the last element (may be a partial event) in the buffer.
      const blocks = buffer.split("\n\n");
      // All blocks except the last are complete events.
      buffer = blocks.pop() ?? "";

      for (const block of blocks) {
        const trimmed = block.trim();
        if (!trimmed) continue;
        const sseEvent = parseSseEvent(trimmed);
        if (sseEvent === null) continue;
        const chunk = toChunk(sseEvent);
        if (chunk !== null) yield chunk;
      }
    }

    // Flush any remaining bytes in the TextDecoder.
    buffer += decoder.decode();

    // Handle a final event with no trailing \n\n (edge case in some proxies).
    const trimmed = buffer.trim();
    if (trimmed) {
      const sseEvent = parseSseEvent(trimmed);
      if (sseEvent !== null) {
        const chunk = toChunk(sseEvent);
        if (chunk !== null) yield chunk;
      }
    }
  } finally {
    reader.releaseLock();
  }
}
