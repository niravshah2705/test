export type Money = {
  amountMinor: number;
  currency: string;
};

export function formatMoney(money: Money): string {
  return `${money.currency} ${(money.amountMinor / 100).toFixed(2)}`;
}
