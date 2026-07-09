export class ApplicationError extends Error {
  constructor(message: string, public readonly code: string) {
    super(message);
  }
}
