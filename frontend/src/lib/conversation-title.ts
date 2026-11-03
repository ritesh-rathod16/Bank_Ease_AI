export const MAX_CONVERSATION_TITLE_LENGTH = 1000;
const GENERATED_TITLE_SUFFIX = "...";

export function normalizeConversationTitle(value: unknown): string {
  if (typeof value !== "string") {
    throw new Error("Conversation title must be text.");
  }

  const title = value.trim().replace(/\s+/g, " ");
  if (!title) {
    throw new Error("Conversation title cannot be empty.");
  }
  if (Array.from(title).length > MAX_CONVERSATION_TITLE_LENGTH) {
    throw new Error(`Conversation title must be ${MAX_CONVERSATION_TITLE_LENGTH} characters or fewer.`);
  }
  return title;
}

export function generateConversationTitle(question: string): string {
  const normalized = question.trim().replace(/\s+/g, " ");
  if (!normalized) return "New conversation";
  const characters = Array.from(normalized);
  if (characters.length <= MAX_CONVERSATION_TITLE_LENGTH) return normalized;

  const prefixLimit = MAX_CONVERSATION_TITLE_LENGTH - GENERATED_TITLE_SUFFIX.length;
  const prefix = characters.slice(0, prefixLimit).join("");
  const lastWordBoundary = prefix.lastIndexOf(" ");
  const meaningfulPrefix = lastWordBoundary > 0 ? prefix.slice(0, lastWordBoundary) : prefix;
  return `${meaningfulPrefix.trimEnd()}${GENERATED_TITLE_SUFFIX}`;
}
