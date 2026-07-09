const reviewStates = [
  {
    status: "unchanged_price",
    heading: "Price confirmed",
    message: "The latest provider price matches your booking snapshot. Payment is available.",
    action: "Continue to payment",
  },
  {
    status: "price_increased",
    heading: "Price increased",
    message: "Payment stays blocked until you explicitly accept the higher fare.",
    action: "Accept new price",
  },
  {
    status: "price_decreased",
    heading: "Price decreased",
    message: "Accept the lower fare before continuing so the payment amount is explicit.",
    action: "Accept new price",
  },
  {
    status: "unavailable_offer",
    heading: "Offer unavailable",
    message: "The fare or itinerary is no longer bookable. Choose another offer.",
    action: "Choose another offer",
  },
  {
    status: "retryable_failure",
    heading: "Price check could not finish",
    message: "A provider timeout or retryable error leaves the draft unchanged and payment blocked.",
    action: "Retry price check",
  },
];

export default function Page() {
  return (
    <main id="main-content">
      <h1>Booking review</h1>
      <p>Review the latest flight price before payment. Payment remains blocked until a valid revalidation succeeds or a changed price is accepted.</p>
      <section aria-labelledby="price-review-heading" aria-live="polite">
        <h2 id="price-review-heading">Price revalidation states</h2>
        <ul>
          {reviewStates.map((state) => (
            <li key={state.status} data-status={state.status}>
              <h3>{state.heading}</h3>
              <p>{state.message}</p>
              <button type="button">{state.action}</button>
            </li>
          ))}
        </ul>
      </section>
      <section aria-labelledby="tokenized-payment-heading">
        <h2 id="tokenized-payment-heading">Secure tokenized payment</h2>
        <p>Card details are collected only inside provider-hosted fields. OFB submits the booking ID, provider payment token, accepted amount, currency, and idempotency key.</p>
        <form action="/api/payments" method="post" data-payment-mode="tokenized">
          <input type="hidden" name="bookingId" value="draft_example" />
          <input type="hidden" name="paymentToken" value="tok_provider_generated_reference" />
          <input type="hidden" name="idempotencyKey" value="booking-draft_example-pay" />
          <input type="hidden" name="amountCents" value="28600" />
          <input type="hidden" name="currency" value="USD" />
          <div data-provider-hosted-card-field="true" aria-label="Provider-hosted card entry" />
          <button type="submit">Pay securely</button>
        </form>
        <p role="note">Never submit raw card number, PAN, CVV, or CVC to OFB APIs.</p>
      </section>
      <section aria-labelledby="confirmation-heading" aria-live="polite">
        <h2 id="confirmation-heading">Booking confirmation</h2>
        <p>Confirmation shows safe itinerary, passenger names, payment status, and ticketing status. Pending ticketing can be refreshed without resubmitting payment.</p>
        <dl>
          <dt>Booking status</dt>
          <dd>Confirmed, ticketing pending, payment declined, or booking failed after payment</dd>
          <dt>Polling endpoint</dt>
          <dd>/api/payments?bookingId=&lt;bookingId&gt;</dd>
        </dl>
      </section>
    </main>
  );
}
