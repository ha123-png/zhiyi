const JSON_NUMBER_PATTERN = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/;
const INTEGER_PATTERN = /^-?(?:0|[1-9]\d*)$/;
const MAX_SAFE_INTEGER_TEXT = String(Number.MAX_SAFE_INTEGER);

function canonicalDecimal(input: string): string {
  const [mantissa, exponentText] = input.toLowerCase().split("e");
  const negative = mantissa.startsWith("-");
  const unsigned = negative ? mantissa.slice(1) : mantissa;
  const [integerPart, fractionPart = ""] = unsigned.split(".");
  let digits = `${integerPart}${fractionPart}`.replace(/^0+/, "") || "0";
  if (digits === "0") return "0";
  let exponent = (exponentText ? Number(exponentText) : 0) - fractionPart.length;
  while (digits.endsWith("0")) {
    digits = digits.slice(0, -1);
    exponent += 1;
  }
  return `${negative ? "-" : ""}${digits}e${exponent}`;
}

/**
 * Parse user-entered numeric cell text without JavaScript's permissive number
 * syntax. Unsafe integers remain canonical decimal strings so JSON transport
 * can preserve every digit for the API's lossless-integer contract.
 */
export function parseNumericInput(input: string, label: string): number | string | null {
  if (input === "") return null;
  if (!JSON_NUMBER_PATTERN.test(input)) {
    throw new Error(`「${label}」需要填写标准数字；逗号、进制前缀和空白都不受支持。`);
  }

  if (INTEGER_PATTERN.test(input)) {
    const digits = input.startsWith("-") ? input.slice(1) : input;
    const unsafe = digits.length > MAX_SAFE_INTEGER_TEXT.length
      || (digits.length === MAX_SAFE_INTEGER_TEXT.length && digits > MAX_SAFE_INTEGER_TEXT);
    if (unsafe) return input;
  }

  const value = Number(input);
  if (!Number.isFinite(value)) throw new Error(`「${label}」需要填写有效数字。`);
  if (canonicalDecimal(input) !== canonicalDecimal(String(value))) {
    throw new Error(`「${label}」超出可精确保留的数字精度。`);
  }
  if (Number.isInteger(value) && !Number.isSafeInteger(value)) {
    throw new Error(`「${label}」的大整数请直接输入十进制整数，不能使用小数或指数写法。`);
  }
  return value;
}
