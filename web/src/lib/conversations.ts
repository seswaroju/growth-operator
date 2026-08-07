// Conversation/message presentation helpers — pure + unit-tested. `direction` is 'inbound' (from
// the customer) or 'outbound' (from the store), matching messages.direction in the backend.

export function isFromStore(direction: string | null): boolean {
  return direction === "outbound";
}

export function senderLabel(direction: string | null): string {
  return direction === "outbound" ? "You" : "Customer";
}

// Short single-line preview of a message body for the inbox list.
export function preview(body: string | null, max = 80): string {
  if (!body) return "No messages yet";
  const oneLine = body.replace(/\s+/g, " ").trim();
  return oneLine.length > max ? oneLine.slice(0, max - 1) + "…" : oneLine;
}
