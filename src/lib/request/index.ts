export function jsonPlaceholder(scope: string, init: ResponseInit = {}): Response {
  return Response.json({ scope, status: "placeholder" }, init);
}

export function notImplemented(scope: string): Response {
  return jsonPlaceholder(scope, { status: 501 });
}
