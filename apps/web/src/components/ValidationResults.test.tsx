import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { expect, it, vi } from "vitest";
import { ValidationResults } from "./ValidationResults";

it("shows a short pass summary without an empty issue placeholder", () => {
  render(<ValidationResults issues={[]} />);
  expect(screen.getByText("校验通过")).toBeInTheDocument();
  expect(screen.queryByRole("listitem")).not.toBeInTheDocument();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});

it("keeps issue identity, long messages, ignore recovery and location actions", () => {
  const toggle = vi.fn(), locate = vi.fn(), parent = vi.fn();
  const issues = [2, 8, 11].map(index => ({ index, row: `明细第 ${index} 行`, field: "金额",
    message: "请对照原件核对数量、单价和金额。".repeat(20), ignored: index === 8, severity: "warning" }));
  render(<div onClick={parent}><ValidationResults issues={issues} onToggleIgnored={toggle} onLocate={locate} /></div>);
  expect(screen.getAllByRole("listitem")).toHaveLength(3);
  fireEvent.click(screen.getByRole("button", { name: "恢复" }));
  expect(toggle).toHaveBeenCalledWith(8);
  parent.mockClear();
  fireEvent.click(screen.getAllByRole("button", { name: "定位相关字段" })[2]);
  expect(locate).toHaveBeenCalledWith(11);
  expect(parent).not.toHaveBeenCalled();
});

it("summarizes the remaining issues and keeps ignored issues available to restore", () => {
  function Review() {
    const [issues, setIssues] = useState([0, 1, 2].map(index => ({
      index, row: `明细第 ${index + 1} 行`, field: "金额", message: "请核对金额", ignored: false,
    })));
    return <ValidationResults issues={issues} onToggleIgnored={index => setIssues(current => current.map(issue =>
      issue.index === index ? { ...issue, ignored: !issue.ignored } : issue))} />;
  }
  render(<Review />);
  expect(screen.getByText("3 处需要核对")).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", { name: "忽略" })[0]);
  expect(screen.getByText("2 处需要核对 · 1 处已忽略")).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", { name: "忽略" })[0]);
  fireEvent.click(screen.getByRole("button", { name: "忽略" }));
  expect(screen.getByText("3 处已忽略")).toBeInTheDocument();
  expect(screen.queryByText("校验通过")).not.toBeInTheDocument();
  expect(screen.getAllByRole("listitem")).toHaveLength(3);
  fireEvent.click(screen.getAllByRole("button", { name: "恢复" })[0]);
  expect(screen.getByText("1 处需要核对 · 2 处已忽略")).toBeInTheDocument();
});
