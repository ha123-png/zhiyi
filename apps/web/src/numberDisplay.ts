const precise = new Intl.NumberFormat("zh-CN", { maximumSignificantDigits: 21 });

/** Keep the supplied value's decimal digits; use explicit notation for extreme magnitudes. */
export function formatNumericValue(value: number | null | undefined): string {
  if (value == null) return "—";
  const magnitude = Math.abs(value);
  return (magnitude > 0 && magnitude < 0.000001) || magnitude >= 1e21
    ? String(value)
    : precise.format(value);
}
