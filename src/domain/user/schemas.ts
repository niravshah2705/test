export function validateUserInput(input: unknown): input is Record<string, unknown> {
  return typeof input === "object" && input !== null;
}
