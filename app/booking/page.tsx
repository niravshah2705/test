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
    </main>
  );
}
