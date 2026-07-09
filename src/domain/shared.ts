export type ServiceResult<T> =
  | { ok: true; value: T }
  | { ok: false; error: string };

export type Identifier = string;
