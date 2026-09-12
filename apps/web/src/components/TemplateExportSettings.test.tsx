import { useRef } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TemplateExportSettings, type TemplateExportHandle } from "./TemplateExportSettings";
import { getLocalExportBinding, saveLocalExportBinding } from "../api";

vi.mock("../api", () => ({ getLocalExportBinding: vi.fn(), saveLocalExportBinding: vi.fn() }));

function Editor() {
  const ref = useRef<TemplateExportHandle>(null);
  return <><TemplateExportSettings ref={ref} templateId="math" /><button onClick={() => { void ref.current?.save().catch(() => undefined); }}>保存模板</button></>;
}

beforeEach(() => {
  vi.mocked(getLocalExportBinding).mockResolvedValue({ revision: 0, enabled: false, parent_path: null, destination: null });
});
afterEach(() => { vi.clearAllMocks(); delete window.pywebview; });

it("starts disabled and uses the template save action after native folder selection", async () => {
  const chooseFolder = vi.fn().mockResolvedValue("D:\\科目");
  const changeGlobal = vi.fn();
  window.pywebview = { api: { choose_folder: chooseFolder, choose_export_directory: changeGlobal } as unknown as NonNullable<NonNullable<Window["pywebview"]>["api"]> };
  vi.mocked(saveLocalExportBinding).mockResolvedValue({ revision: 1, enabled: true, parent_path: "D:\\科目", destination: "D:\\科目\\数学" });
  render(<Editor />);
  const toggle = await screen.findByRole("checkbox");
  await waitFor(() => expect(toggle).toBeEnabled());
  expect(toggle).not.toBeChecked();
  fireEvent.click(toggle);
  fireEvent.click(screen.getByRole("button", { name: "选择文件夹" }));
  await waitFor(() => expect(screen.getByText("D:\\科目")).toBeInTheDocument());
  expect(changeGlobal).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "保存模板" }));
  await waitFor(() => expect(saveLocalExportBinding).toHaveBeenCalledOnce());
  expect(saveLocalExportBinding).toHaveBeenCalledWith("math", { expected_revision: 0, enabled: true, parent_path: "D:\\科目" });
  expect(screen.getByText(/清除知意数据不会删除/)).toBeInTheDocument();
});

it("keeps entered path when save fails and does not claim success", async () => {
  vi.mocked(saveLocalExportBinding).mockRejectedValue(new Error("目标文件夹不可访问"));
  render(<Editor />);
  await waitFor(() => expect(screen.getByRole("checkbox")).toBeEnabled());
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "设置服务端目录" }));
  fireEvent.change(screen.getByLabelText("知意服务所在电脑的绝对路径"), { target: { value: "D:\\旧路径" } });
  fireEvent.click(screen.getByRole("button", { name: "保存模板" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("目标文件夹不可访问");
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
  expect(screen.getByLabelText("知意服务所在电脑的绝对路径")).toHaveValue("D:\\旧路径");
});
