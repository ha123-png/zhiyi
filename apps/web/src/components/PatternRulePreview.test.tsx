import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { previewTemplatePattern } from "../api";
import { PatternRulePreview } from "./PatternRulePreview";
vi.mock("../api", () => ({ previewTemplatePattern: vi.fn() }));

it("checks with the server and hides the result after changing the value", async () => {
  vi.mocked(previewTemplatePattern).mockResolvedValue({ valid: true, matches: true, message: "整段匹配通过。" });
  render(<PatternRulePreview pattern={"PO-\\d{3}"} disabled={false} />);
  fireEvent.change(screen.getByLabelText("试填一个值"), { target: { value: "PO-001" } });
  fireEvent.click(screen.getByRole("button", { name: "检查格式" }));
  expect(await screen.findByRole("status")).toHaveTextContent("整段匹配通过");
  expect(previewTemplatePattern).toHaveBeenCalledWith("PO-\\d{3}", "PO-001");
  fireEvent.change(screen.getByLabelText("试填一个值"), { target: { value: "PO-01" } });
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
});
