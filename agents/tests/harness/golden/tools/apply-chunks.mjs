// Upstream cloudflare/agents@455b47e experimental/channels/apply-chunks.ts with types stripped.
export function applyChunks(message, chunks) {
  const parts = message.parts.map((part) => ({ ...part }));
  let metadata = message.metadata;
  const open = new Map();
  const toolPart = (toolCallId) =>
    parts.find((part) => part.type === "tool" && part.toolCallId === toolCallId);
  for (const chunk of chunks) {
    switch (chunk.type) {
      case "text-start":
      case "reasoning-start": {
        const kind = chunk.type === "text-start" ? "text" : "reasoning";
        const part = {
          type: kind,
          text: "",
          ...(chunk.providerMetadata !== undefined && { providerMetadata: chunk.providerMetadata })
        };
        open.set(`${kind}:${chunk.id}`, part);
        parts.push(part);
        break;
      }
      case "text-delta":
      case "reasoning-delta": {
        const kind = chunk.type === "text-delta" ? "text" : "reasoning";
        const part = open.get(`${kind}:${chunk.id}`);
        if (part) part.text += chunk.delta;
        break;
      }
      case "text-end":
      case "reasoning-end": {
        const key = `${chunk.type.split("-")[0]}:${chunk.id}`;
        const part = open.get(key);
        if (part && chunk.providerMetadata !== undefined) {
          part.providerMetadata = { ...part.providerMetadata, ...chunk.providerMetadata };
        }
        open.delete(key);
        break;
      }
      case "tool-input-start":
      case "tool-input-available":
      case "tool-input-error": {
        const { type, ...call } = chunk;
        const existing = toolPart(chunk.toolCallId);
        const next = {
          ...existing,
          ...call,
          type: "tool",
          state: type === "tool-input-start" ? "input-streaming" : type === "tool-input-available" ? "input-available" : "output-error"
        };
        if (existing) parts[parts.indexOf(existing)] = next;
        else parts.push(next);
        break;
      }
      case "tool-input-delta":
        break;
      case "tool-approval-request":
      case "tool-output-available":
      case "tool-output-error":
      case "tool-output-denied": {
        const part = toolPart(chunk.toolCallId);
        if (!part) break;
        if (chunk.type === "tool-approval-request") {
          part.state = "approval-requested";
          part.approval = { id: chunk.approvalId };
        } else if (chunk.type === "tool-output-available") {
          part.state = "output-available";
          part.output = chunk.output;
          part.preliminary = chunk.preliminary;
        } else if (chunk.type === "tool-output-error") {
          part.state = "output-error";
          part.errorText = chunk.errorText;
        } else {
          part.state = "output-denied";
        }
        break;
      }
      case "data": {
        if (chunk.transient) break;
        const { transient: _transient, ...part } = chunk;
        const index = parts.findIndex((p) => p.type === "data" && p.name === chunk.name && p.id !== undefined && p.id === chunk.id);
        if (index === -1) parts.push(part);
        else parts[index] = part;
        break;
      }
      case "metadata":
        metadata = { ...metadata, ...chunk.metadata };
        break;
      default:
        parts.push(chunk);
    }
  }
  return { ...message, parts, ...(metadata && { metadata }) };
}
