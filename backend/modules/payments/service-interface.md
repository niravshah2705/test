# PaymentService

```ts
interface PaymentService {
  createIntent(request: CreatePaymentIntentRequest, actor: AuthContext): Promise<PaymentIntent>;
  authorize(intentId: PaymentIntentId, actor: AuthContext): Promise<PaymentIntent>;
  capture(intentId: PaymentIntentId, actor: AuthContext): Promise<PaymentIntent>;
  refund(intentId: PaymentIntentId, request: RefundRequest, actor: AuthContext): Promise<PaymentIntent>;
  getIntent(intentId: PaymentIntentId, actor: AuthContext): Promise<PaymentIntent>;
}
```

## Contracts

```ts
interface CreatePaymentIntentRequest {
  amount: Money;
  purpose: 'flight-booking' | 'taxi-booking';
  customerUserId: UserId;
}

interface RefundRequest {
  amount?: Money;
  reason: string;
}

interface PaymentIntent {
  id: PaymentIntentId;
  amount: Money;
  status: 'created' | 'authorized' | 'captured' | 'voided' | 'refunded' | 'failed';
  providerReference?: string;
}
```
