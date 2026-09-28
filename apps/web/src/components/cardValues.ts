export function displayCardValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "boolean") return value ? "是" : "否";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

export function contentType(filename: string): string {
  const extension = filename.split(".").pop()?.toLowerCase() ?? "";
  return ({pdf: "application/pdf", txt: "text/plain", md: "text/markdown",
    docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", webp: "image/webp",
    bmp: "image/bmp", gif: "image/gif", tif: "image/tiff", tiff: "image/tiff",
  } as Record<string, string>)[extension] ?? "application/octet-stream";
}
