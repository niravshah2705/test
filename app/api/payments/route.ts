type PaymentPayload = {
  bookingId?: string;
  paymentToken?: string;
  paymentReference?: string;
  idempotencyKey?: string;
  amountCents?: number;
  currency?: string;
  cardNumber?: string;
  cvv?: string;
  cvc?: string;
  pan?: string;
};

const TOKEN_PATTERN = /^(tok|pm|ref)_[A-Za-z0-9_-]{8,80}$/;
const IDEMPOTENCY_PATTERN = /^[A-Za-z0-9_.:-]{8,120}$/;
const RAW_CARD_FIELDS = ["cardNumber", "cvv", "cvc", "pan"] as const;

export async function GET(request: Request) {
  const bookingId = new URL(request.url).searchParams.get("bookingId");
  if (!bookingId) {
    return apiError(400, "validation_error", "bookingId is required for payment status polling.", { bookingId: ["bookingId is required."] });
  }

  return apiSuccess({
    bookingId,
    status: "ticketing_pending",
    payment: { status: "captured" },
    polling: { enabled: true, href: `/api/payments?bookingId=${encodeURIComponent(bookingId)}` },
  });
}

export async function POST(request: Request) {
  let payload: PaymentPayload;
  try {
    payload = await request.json();
  } catch {
    return apiError(400, "invalid_json", "Request body must be valid JSON.");
  }

  const errors = validatePaymentPayload(payload);
  if (Object.keys(errors).length > 0) {
    return apiError(400, "validation_error", "Payment finalization failed validation.", errors);
  }

  if (payload.paymentToken === "tok_declined_fixture") {
    return apiSuccess({ bookingId: payload.bookingId, status: "payment_declined", payment: { status: "declined" }, order: null, polling: { enabled: false } }, 202);
  }

  return apiSuccess(
    {
      bookingId: payload.bookingId,
      status: "ticketing_pending",
      payment: { status: "captured", amount: { amountCents: payload.amountCents, currency: payload.currency } },
      order: { status: "ticketing_pending" },
      polling: { enabled: true, href: `/api/payments?bookingId=${encodeURIComponent(String(payload.bookingId))}` },
    },
    202,
  );
}

function validatePaymentPayload(payload: PaymentPayload): Record<string, string[]> {
  const errors: Record<string, string[]> = {};
  for (const field of RAW_CARD_FIELDS) {
    if (payload[field]) {
      errors[field] = ["Raw card data must stay inside provider-hosted tokenized inputs."];
    }
  }
  const token = payload.paymentToken ?? payload.paymentReference ?? "";
  if (!TOKEN_PATTERN.test(token)) {
    errors.paymentToken = ["A provider payment token/reference is required."];
  }
  if (!payload.bookingId) {
    errors.bookingId = ["bookingId is required."];
  }
  if (!payload.idempotencyKey || !IDEMPOTENCY_PATTERN.test(payload.idempotencyKey)) {
    errors.idempotencyKey = ["A stable idempotency key is required."];
  }
  if (!Number.isInteger(payload.amountCents)) {
    errors.amountCents = ["Accepted amount in cents is required."];
  }
  if (!payload.currency) {
    errors.currency = ["Accepted currency is required."];
  }
  return errors;
}

function apiSuccess(data: unknown, status = 200): Response {
  return Response.json({ success: true, data, error: null }, { status });
}

function apiError(status: number, code: string, message: string, fields?: Record<string, string[]>): Response {
  return Response.json({ success: false, data: null, error: { code, message, ...(fields ? { fields } : {}) } }, { status });
}
