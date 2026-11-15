import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { DataTablePage } from "./DataTablePage";

const table = {id: "outcome-table", name: "合成导入表", template_key: "manual", template_version: "1",
  document_kind: "custom", row_count: 0, created_at: ""};
const json = (value: unknown, status=200) => new Response(JSON.stringify(value), {status, headers: {"Content-Type":"application/json"}});

it("explains why a merged table cannot be appended to while allowing a separate table import", async () => {
  const merged = {...table, name: "验收合并表", merge_sources: ["source-a", "source-b"]};
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
    const path = new URL(String(input), "http://localhost").pathname;
    if (path.endsWith("/templates") || path.endsWith("/views")) return json([]);
    if (path.endsWith("/tables")) return json([merged]);
    return json({...merged, page: 1, page_size: 10, columns: [], rows: []});
  }));
  const view = render(<DataTablePage />);
  await screen.findByRole("heading", {name: "验收合并表"});
  fireEvent.click(screen.getByRole("button", {name: "导入"}));
  const append = screen.getByRole("button", {name: /追加到当前表/});
  const [appendInput, createInput] = view.container.querySelectorAll<HTMLInputElement>('input[type="file"]');
  const chooseAppend = vi.spyOn(appendInput, "click");
  const chooseNew = vi.spyOn(createInput, "click");
  expect(append).toBeDisabled();
  expect(append).toHaveTextContent("合并表只读，请到原表追加");
  fireEvent.click(append);
  expect(chooseAppend).not.toHaveBeenCalled();
  const create = screen.getByRole("button", {name: /从文件新建表/});
  expect(create).toBeEnabled();
  fireEvent.click(create);
  expect(chooseNew).toHaveBeenCalledOnce();
});

it.each(["transport", "server-error", "invalid-result", "invalid-sheet-error"])("keeps %s import outcome unknown and recovery never repeats POST", async (failure) => {
  let posts=0, written=false, readsFail=false;
  vi.stubGlobal("fetch",vi.fn(async (input: string | URL, init?: RequestInit) => {
    const url=new URL(String(input),"http://localhost");
    if (init?.method === "POST" && url.pathname.endsWith("/import")) {
      posts++; written=true;
      if (failure === "transport") throw new TypeError("Failed to fetch");
      if (failure === "invalid-sheet-error") return json({detail:{code:"select_sheet",sheets:["旧响应"]}},503);
      return failure === "server-error" ? json({detail:"响应未知"},503) : json({});
    }
    if (url.pathname.endsWith("/templates") || url.pathname.endsWith("/views")) return json([]);
    if (url.pathname.endsWith("/tables")) return readsFail ? json({detail:"目录暂不可用"},503) : json([{...table,row_count:written?1:0}]);
    return readsFail ? json({detail:"数据暂不可用"},503) : json({...table,row_count:written?1:0,page:1,page_size:10,
      columns:[{key:"item",label:"项目",value_type:"text",section:"item"}],
      rows:written?[{id:1,task_id:null,item_index:1,version:0,review_pending:false,values:{item:"已提交合成行"},created_at:"",updated_at:""}]:[]});
  }));
  const view=render(<DataTablePage />);
  await waitFor(()=>expect(screen.getByRole("button",{name:"导入"})).toBeEnabled());
  const fileInput=view.container.querySelector('input[type="file"]')!;
  const file=new File(["项目\n已提交合成行"],"合成.csv");
  fireEvent.change(fileInput,{target:{files:[file]}});
  fireEvent.click(await screen.findByRole("button",{name:"仓库事项"}));
  expect(screen.getByRole("dialog",{name:"仓库事项"})).toHaveTextContent(/导入结果尚未确认，可能已经写入/);
  expect(posts).toBe(1);
  expect(screen.getByRole("button",{name:"导入"})).toBeDisabled();
  fireEvent.change(fileInput,{target:{files:[file]}});
  expect(posts).toBe(1);
  readsFail=true;
  fireEvent.click(screen.getByRole("button",{name:"重新读取目标表核对"}));
  await screen.findByRole("heading",{name:"暂时无法读取数据表列表"});
  fireEvent.click(await screen.findByRole("button",{name:"仓库事项"}));
  expect(screen.getByRole("dialog",{name:"仓库事项"})).toHaveTextContent(/导入结果尚未确认/);
  expect(posts).toBe(1);
  readsFail=false;
  fireEvent.click(screen.getByRole("button",{name:"重新读取目标表核对"}));
  await screen.findByRole("cell",{name:"编辑 项目，当前值 已提交合成行"});
  fireEvent.click(await screen.findByRole("button",{name:"仓库事项"}));
  expect(screen.getByRole("dialog",{name:"仓库事项"})).toHaveTextContent(/导入结果尚未确认/);
  expect(posts).toBe(1);
  fireEvent.click(screen.getByRole("button",{name:"已核对，继续操作"}));
  await waitFor(()=>expect(screen.getByRole("button",{name:"导入"})).toBeEnabled());
  expect(screen.queryByText(/导入结果尚未确认/)).not.toBeInTheDocument();
  expect(posts).toBe(1);
});

it("closes a worksheet chooser after uncertain submission instead of retaining a repeat-POST action", async () => {
  let posts=0;
  vi.stubGlobal("fetch",vi.fn(async (input: string | URL, init?: RequestInit) => {
    const url=new URL(String(input),"http://localhost");
    if (init?.method === "POST") {
      posts++;
      if (posts === 1) return json({detail:{code:"select_sheet",sheets:["第一页","第二页"]}},409);
      throw new TypeError("Response lost after commit");
    }
    if (url.pathname.endsWith("/templates") || url.pathname.endsWith("/views")) return json([]);
    if (url.pathname.endsWith("/tables")) return json([table]);
    return json({...table,page:1,page_size:10,columns:[],rows:[]});
  }));
  const view=render(<DataTablePage />);
  await waitFor(()=>expect(screen.getByRole("button",{name:"导入"})).toBeEnabled());
  fireEvent.change(view.container.querySelectorAll('input[type="file"]')[1],{target:{files:[new File(["synthetic"],"合成多页.xlsx")]}});
  await screen.findByRole("dialog",{name:"选择要导入的工作表"});
  fireEvent.change(screen.getByRole("combobox",{name:"工作表"}),{target:{value:"第二页"}});
  fireEvent.click(screen.getByRole("button",{name:"导入此工作表"}));
  const entry=await screen.findByRole("button",{name:"仓库事项"});
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  fireEvent.click(entry);
  expect(screen.getByRole("dialog",{name:"仓库事项"})).toHaveTextContent(/导入结果尚未确认/);
  expect(screen.queryByRole("button",{name:"导入此工作表"})).not.toBeInTheDocument();
  expect(posts).toBe(2);
  fireEvent.click(screen.getByRole("button",{name:"重新读取目录核对"}));
  fireEvent.click(await screen.findByRole("button",{name:"仓库事项"}));
  await waitFor(()=>expect(screen.getByRole("button",{name:"已核对，继续操作"})).toBeEnabled());
  expect(posts).toBe(2);
});

it("preserves a definite validation rejection without reporting a committed or unknown outcome", async () => {
  let posts=0;
  vi.stubGlobal("fetch",vi.fn(async (input: string | URL, init?: RequestInit) => {
    const url=new URL(String(input),"http://localhost");
    if (init?.method === "POST") { posts++; return json({detail:"合成列格式无效"},422); }
    if (url.pathname.endsWith("/templates") || url.pathname.endsWith("/views")) return json([]);
    if (url.pathname.endsWith("/tables")) return json([table]);
    return json({...table,page:1,page_size:10,columns:[],rows:[]});
  }));
  const view=render(<DataTablePage />);
  await waitFor(()=>expect(screen.getByRole("button",{name:"导入"})).toBeEnabled());
  fireEvent.change(view.container.querySelector('input[type="file"]')!,{target:{files:[new File(["invalid"],"无效.csv")]}});
  await screen.findByText("合成列格式无效");
  expect(screen.queryByText(/导入结果尚未确认/)).not.toBeInTheDocument();
  expect(screen.getByRole("button",{name:"导入"})).toBeEnabled();
  expect(posts).toBe(1);
});
