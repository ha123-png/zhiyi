import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TaskQueuePage } from "./TaskQueuePage";
import { TASK_GROUPS, taskGroup } from "../taskPresentation";
import type { Task, TaskGroup, TaskSummary } from "../types";
import type { TaskOverview } from "../useTaskOverview";

const base: Task = { id:"task", filename:"文件.pdf", content_type:"application/pdf", size_bytes:100, sha256:"digest", template_mode:"smart", template_id:null, template_version:null, candidate_templates:[], duplicate_of_task_id:null, status:"completed", created_at:"2026-11-02T08:00:00Z", updated_at:"2026-11-02T08:00:00Z" };
const task = (id: string, status: Task["status"], extra: Partial<Task> = {}): Task => ({...base,id,filename:`${id}.pdf`,status,...extra});
function summary(tasks: Task[]): TaskSummary {
  const groups = Object.fromEntries(TASK_GROUPS.map(item=>[item.id,item.id==="all" ? tasks.length : tasks.filter(task=>taskGroup(task)===item.id).length])) as Record<TaskGroup,number>;
  return { total:tasks.length, completed:groups.completed, needs_review:tasks.filter(task=>task.status==="needs_review").length, failed:groups.failed, cancelled:groups.cancelled, group_counts:groups };
}
function overview(tasks: Task[], extra: Partial<TaskOverview> = {}): TaskOverview { return {tasks,summary:summary(tasks),queuePaused:false,state:"ready",error:null,revision:1,refresh:vi.fn(async()=>{}),...extra}; }
const json = (data:unknown,status=200)=>new Response(JSON.stringify(data),{status,headers:{"Content-Type":"application/json"}});
function server(tasks:Task[], override?: (url:URL,init?:RequestInit)=>Response|Promise<Response>|undefined) {
  const fetch=vi.fn(async (input:RequestInfo|URL,init?:RequestInit)=>{
    const url=new URL(String(input),"http://localhost");
    const result=override?.(url,init); if(result!==undefined)return result;
    if(url.pathname.endsWith("/tasks/summary"))return json(summary(tasks));
    if(url.pathname.endsWith("/queue/status"))return json({paused:false});
    if(url.pathname.endsWith("/templates"))return json([]);
    if(url.pathname.endsWith("/tasks")) {
      let items=tasks.filter(item=>!url.searchParams.get("group")||url.searchParams.get("group")==="all"||taskGroup(item)===url.searchParams.get("group"));
      if(url.searchParams.get("active_only"))items=items.filter(item=>["created","queued","processing","validating","paused","waiting_for_template","needs_review"].includes(item.status));
      if(url.searchParams.get("search"))items=items.filter(item=>item.filename.includes(url.searchParams.get("search")!));
      const offset=Number(url.searchParams.get("offset")||0);return json(items.slice(offset,offset+Number(url.searchParams.get("limit")||100)));
    }
    return json({});
  });vi.stubGlobal("fetch",fetch);return fetch;
}
async function more(name:string) { fireEvent.keyDown(screen.getByRole("button",{name:`更多操作 ${name}`}),{key:"Enter"}); await screen.findByRole("menu"); }

describe("TaskQueuePage list workspace",()=>{
  it("uses the approved filter order and one mutually exclusive classification",async()=>{
    const items=[task("running","processing"),task("done","completed"),task("review","needs_review"),task("failed","failed"),task("cancelled","cancelled"),task("copy","completed",{file_export:{status:"failed",parent_path:null,destination:null,actual_path:null,confirmed_name:null,error_code:"destination_exists",error_message:"目录已有同名文件"}})];
    server(items);render(<TaskQueuePage overview={overview(items)} onOpenTask={vi.fn()}/>);
    await screen.findByText("copy.pdf");
    expect(within(screen.getByRole("group",{name:"任务状态筛选"})).getAllByRole("button").map(button=>button.textContent?.replace(/\d+|—/g,""))).toEqual(["全部","进行中","已完成","待确认","失败","已取消"]);
    fireEvent.click(screen.getByRole("button",{name:/^已完成/}));await waitFor(()=>expect(screen.queryByText("copy.pdf")).not.toBeInTheDocument());expect(screen.getByText("done.pdf")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button",{name:/^待确认/}));await screen.findByText("copy.pdf");expect(screen.queryByText("done.pdf")).not.toBeInTheDocument();expect(screen.getByText("review.pdf")).toBeInTheDocument();
  });
  it("shows partial page selection and resets it after selecting all, clearing, and filtering",async()=>{
    const items=[task("failed","failed"),task("done","completed")];
    server(items);render(<TaskQueuePage overview={overview(items)}/>);
    await screen.findByText("done.pdf");
    const header=screen.getByRole("checkbox",{name:"选择本页任务"}) as HTMLInputElement;
    const failed=screen.getByRole("checkbox",{name:"选择 failed.pdf"});
    const done=screen.getByRole("checkbox",{name:"选择 done.pdf"});
    const table=screen.getByRole("table");
    const headings=within(table).getAllByRole("columnheader");
    expect(header).not.toBeChecked();expect(header.indeterminate).toBe(false);

    fireEvent.click(failed);
    expect(header).not.toBeChecked();expect(header.indeterminate).toBe(true);
    expect(screen.getByText("本页已选 1 个文件")).toBeInTheDocument();
    fireEvent.click(header);
    expect(header).toBeChecked();expect(header.indeterminate).toBe(false);
    expect(failed).toBeChecked();expect(done).toBeChecked();
    expect(screen.getByText("本页已选 2 个文件")).toBeInTheDocument();
    expect(within(table).getAllByRole("columnheader")).toEqual(headings);
    expect(within(headings[1]).getByText("本页已选 2 个文件")).toBeInTheDocument();
    fireEvent.click(within(headings.at(-1)!).getByRole("button",{name:"删除"}));
    const deletion=await screen.findByRole("dialog",{name:"删除所选文件和数据"});
    expect(deletion).toHaveTextContent("平台内部原件、当前数据和文件修订历史");
    fireEvent.click(within(deletion).getByRole("button",{name:"取消"}));

    expect(screen.queryByRole("button",{name:"取消选择"})).not.toBeInTheDocument(); fireEvent.click(header);
    expect(header).not.toBeChecked();expect(header.indeterminate).toBe(false);
    expect(failed).not.toBeChecked();expect(done).not.toBeChecked();
    fireEvent.click(failed);expect(header.indeterminate).toBe(true);
    fireEvent.click(screen.getByRole("button",{name:/^已完成/}));
    await waitFor(()=>expect(screen.queryByText("failed.pdf")).not.toBeInTheDocument());
    expect(header).not.toBeChecked();expect(header.indeterminate).toBe(false);
    expect(screen.getByRole("checkbox",{name:"选择 done.pdf"})).not.toBeChecked();
    expect(screen.queryByText(/本页已选/)).not.toBeInTheDocument();
  });
  it("shares the supplied overview and reads only the scoped page and its counts",async()=>{
    const fetch=server([task("done","completed")]);render(<TaskQueuePage overview={overview([task("done","completed")])}/>);await screen.findByText("done.pdf");
    const urls=fetch.mock.calls.map(([url])=>new URL(String(url),"http://localhost")).filter(url=>url.pathname.includes("/tasks"));
    expect(urls).toHaveLength(2);
    expect(urls.map(url=>url.searchParams.get("terminal_since"))[0]).toBe(urls[1].searchParams.get("terminal_since"));
    expect(urls.some(url=>url.searchParams.has("active_only"))).toBe(false);
  });
  it.each(["copy","move"] as const)("shows automatic %s export as running with read-only task actions",async mode=>{
    const item=task("automatic","completed",{has_result:true,file_export:{mode,status:"exporting",parent_path:null,destination:null,actual_path:null,confirmed_name:null,error_code:null,error_message:null}});
    const fetch=server([item]);const open=vi.fn();render(<TaskQueuePage overview={overview([item])} onOpenTask={open}/>);
    expect(await screen.findByText(mode==="move"?"正在归档":"正在导出")).toBeInTheDocument();
    expect(taskGroup(item)).toBe("active");fireEvent.click(screen.getByRole("button",{name:"对照 automatic.pdf"}));expect(open).toHaveBeenCalledWith(item);
    await more("automatic.pdf");expect(screen.queryByRole("menuitem",{name:"取消任务"})).not.toBeInTheDocument();expect(fetch.mock.calls.some(([,init])=>init?.method==="POST")).toBe(false);
  });
  it("does not claim a failed initial read is empty or offer a new-start action",async()=>{
    server([],url=>url.pathname.endsWith("/tasks")?json({detail:"合成读取失败"},503):undefined);
    render(<TaskQueuePage overview={overview([],{state:"error",summary:null,error:"概览读取失败"})} onNavigateExtract={vi.fn()}/>);
    fireEvent.click(await screen.findByRole("button",{name:"任务读取失败"}));expect(screen.getByRole("dialog",{name:"任务读取失败"})).toHaveTextContent("尚未读取到任务数据");expect(screen.queryByText("还没有任务")).not.toBeInTheDocument();expect(screen.queryByRole("button",{name:"开始提取"})).not.toBeInTheDocument();
    expect(within(screen.getByRole("group",{name:"任务状态筛选"})).getAllByText("—")).toHaveLength(6);
  });
  it("offers a new-start action only after an actual successful empty list",async()=>{
    let finish!:(value:Response)=>void;server([],url=>url.pathname.endsWith("/tasks")?new Promise(resolve=>{finish=resolve;}):undefined);
    const navigate=vi.fn();render(<TaskQueuePage overview={overview([])} onNavigateExtract={navigate}/>);expect(screen.queryByRole("button",{name:"开始提取"})).not.toBeInTheDocument();
    await act(async()=>finish(json([])));fireEvent.click(await screen.findByRole("button",{name:"开始提取"}));expect(navigate).toHaveBeenCalledOnce();
  });
  it("retains successful content when a refresh fails and gives a retry",async()=>{
    const items=[task("retained","completed")];let fail=false;server(items,url=>url.pathname.endsWith("/tasks")&&fail?json({detail:"暂时不可用"},503):undefined);
    const state=overview(items);const view=render(<TaskQueuePage overview={state}/>);await screen.findByText("retained.pdf");fail=true;view.rerender(<TaskQueuePage overview={{...state,revision:2}}/>);
    fireEvent.click(await screen.findByRole("button",{name:"任务读取失败"}));expect(screen.getByRole("dialog",{name:"任务读取失败"})).toHaveTextContent("保留上次读取的内容");expect(screen.getByText("retained.pdf")).toBeInTheDocument();
    fail=false;fireEvent.click(screen.getByRole("button",{name:"重新读取"}));await waitFor(()=>expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });
  it("uses a typed pending action and never retries waiting-for-template tasks",async()=>{
    const items=[task("pending","waiting_for_template",{pending_reason:"template",pending_action:"select_template",candidate_templates:[{id:"tpl",name:"推荐模板",description:"任意中文文案",version:1}]})];const fetch=server(items);render(<TaskQueuePage overview={overview(items)}/>);await screen.findByText("pending.pdf");
    fireEvent.click(screen.getByRole("button",{name:"选模板 pending.pdf"}));const dialog=await screen.findByRole("dialog",{name:"选择模板"});
    fireEvent.change(within(dialog).getByRole("combobox",{name:"选择模板"}),{target:{value:"tpl"}});fireEvent.click(within(dialog).getByRole("button",{name:"确认"}));
    await waitFor(()=>expect(fetch.mock.calls.some(([url,init])=>String(url).endsWith("/pending/template")&&init?.method==="POST")).toBe(true));
    expect(fetch.mock.calls.some(([url])=>String(url).includes("/retry"))).toBe(false);
  });
  it("does not infer a target-table action from Chinese display text",async()=>{
    const item=task("unknown","waiting_for_template",{pending_reason:"unknown",pending_action:null,candidate_templates:[{id:"tpl",version:1,name:"模板",description:"指定表不符"}]});const open=vi.fn();const fetch=server([item]);render(<TaskQueuePage overview={overview([item])} onOpenTask={open}/>);
    await screen.findByText("unknown.pdf"); expect(screen.queryByRole("button",{name:"查看 unknown.pdf"})).not.toBeInTheDocument(); expect(screen.queryByRole("button",{name:"选数据表 unknown.pdf"})).not.toBeInTheDocument(); expect(open).not.toHaveBeenCalled();expect(fetch.mock.calls.some(([,init])=>init?.method==="POST")).toBe(false);
  });
  it("submits the observed target-table review version, not a guessed zero",async()=>{
    const item=task("table","waiting_for_template",{pending_reason:"target_table",pending_action:"confirm_target_table",pending_review_version:7,candidate_templates:[{id:"tpl",version:1,name:"模板",description:"可任意修改文案"}]});
    const fetch=server([item],url=>url.pathname.endsWith("/tables")?json([{id:"table-id",name:"兼容表",template_key:"tpl"}]):undefined);render(<TaskQueuePage overview={overview([item])}/>);
    fireEvent.click(await screen.findByRole("button",{name:"选数据表 table.pdf"}));const dialog=await screen.findByRole("dialog",{name:"选择数据表"});await within(dialog).findByRole("option",{name:"兼容表"});
    fireEvent.change(within(dialog).getByRole("combobox"),{target:{value:"table-id"}});fireEvent.click(within(dialog).getByRole("button",{name:"确认"}));
    await waitFor(()=>{const call=fetch.mock.calls.find(([url,init])=>String(url).endsWith("/table/template")&&init?.method==="POST");expect(JSON.parse(String(call?.[1]?.body))).toEqual({template_id:null,target_table_id:"table-id",expected_review_version:7});});
  });
  it("keeps missing target versions non-actionable",async()=>{
    const item=task("table","waiting_for_template",{pending_action:"confirm_target_table",pending_review_version:null,candidate_templates:[{id:"tpl",version:1,name:"模板",description:""}]});server([item],url=>url.pathname.endsWith("/tables")?json([{id:"t",name:"兼容表",template_key:"tpl"}]):undefined);render(<TaskQueuePage overview={overview([item])}/>);
    fireEvent.click(await screen.findByRole("button",{name:"选数据表 table.pdf"}));await screen.findByRole("option",{name:"兼容表"});fireEvent.change(within(screen.getByRole("dialog")).getByRole("combobox"),{target:{value:"t"}});expect(screen.getByRole("button",{name:"确认"})).toBeDisabled();
  });
  it("blocks unknown template choices but permits a task recommendation after catalog failure",async()=>{
    const item=task("pick","waiting_for_template",{pending_action:"select_template",candidate_templates:[{id:"tpl",version:1,name:"推荐模板",description:""}]});server([item],url=>url.pathname.endsWith("/templates")?json({detail:"目录失败"},503):undefined);render(<TaskQueuePage overview={overview([item])}/>);
    fireEvent.click(await screen.findByRole("button",{name:"选模板 pick.pdf"}));expect(await screen.findByRole("alert")).toHaveTextContent("目录失败");expect(screen.getByRole("button",{name:"确认"})).toBeDisabled();fireEvent.change(screen.getByRole("combobox",{name:"选择模板"}),{target:{value:"tpl"}});expect(screen.getByRole("button",{name:"确认"})).toBeEnabled();
  });
  it("pauses only the row task, and refuses single resume while the whole queue is paused",async()=>{
    const item=task("running","processing");const fetch=server([item]);const view=render(<TaskQueuePage overview={overview([item])}/>);fireEvent.click(await screen.findByRole("button",{name:"暂停 running.pdf"}));
    await waitFor(()=>expect(fetch.mock.calls.some(([url,init])=>String(url).endsWith("/running/pause")&&init?.method==="POST")).toBe(true));expect(fetch.mock.calls.some(([url])=>String(url).includes("/queue/pause"))).toBe(false);
    view.unmount();const paused=task("paused","paused");server([paused]);render(<TaskQueuePage overview={overview([paused],{queuePaused:true})}/>);expect(await screen.findByRole("button",{name:"继续 paused.pdf"})).toBeDisabled();
  });
  it("preserves deletion failure in the confirmation and retries only its captured scope",async()=>{
    const item=task("failed","failed");let tries=0;const fetch=server([item],(url,init)=>url.pathname.endsWith("/failed")&&init?.method==="DELETE"?json(++tries===1?{detail:"删除失败"}:{kept_rows:0},tries===1?503:200):undefined);
    render(<TaskQueuePage overview={overview([item])}/>);await screen.findByText("failed.pdf");await more("failed.pdf");fireEvent.click(screen.getByRole("menuitem",{name:"删除…"}));
    const dialog=await screen.findByRole("dialog",{name:"删除文件记录"});expect(dialog).toHaveTextContent("平台内部原件、当前数据");
    const confirm = within(dialog).getByRole("button", { name: "确认删除" });
    expect(confirm).toBeDisabled();
    fireEvent.change(within(dialog).getByRole("textbox", { name: "输入 failed.pdf 确认删除" }), { target: { value: "failed.pdf" } });
    fireEvent.click(confirm);expect(await within(dialog).findByRole("alert")).toHaveTextContent("删除失败");
    fireEvent.click(within(dialog).getByRole("button",{name:"确认删除"}));await waitFor(()=>expect(screen.queryByRole("dialog")).not.toBeInTheDocument());expect(fetch.mock.calls.filter(([,init])=>init?.method==="DELETE")).toHaveLength(2);
  });
  it("confirms the captured batch once and reports files skipped by the server", async () => {
    const items = [task("first", "completed"), task("second", "completed")];
    const duplicates = [{ id: "pending", name: "再上传.pdf", size: 100, selection: "smart",
      existingTaskId: "first", createdAt: base.created_at }];
    const fetch = server(items, (url, init) =>
      url.pathname.endsWith("/batch-delete") && init?.method === "POST" ? json({ deleted: 1, skipped: 1, kept_rows: 0 }) : undefined);
    render(<TaskQueuePage overview={overview(items)} duplicates={duplicates} />);
    await screen.findByText("first.pdf");
    fireEvent.click(screen.getByRole("checkbox", { name: "选择本页任务" }));
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    const dialog = within(await screen.findByRole("dialog", { name: "删除所选文件和数据" }));
    expect(dialog.getByRole("heading", { name: "删除所选文件和数据" })).toHaveClass("confirm-danger-title");
    expect(within(dialog.getByRole("list")).getAllByRole("listitem").map(item => item.textContent))
      .toEqual(["first.pdf", "second.pdf", "再上传.pdf"]);
    const confirm = dialog.getByRole("button", { name: "确认删除" });
    expect(confirm).toBeDisabled();
    fireEvent.change(dialog.getByRole("textbox", { name: "输入 删除所选 确认删除" }), { target: { value: "删除所选" } });
    fireEvent.click(confirm);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const writes = fetch.mock.calls.filter(([, init]) => init?.method === "POST" || init?.method === "DELETE");
    expect(writes).toHaveLength(1);
    expect(String(writes[0][0])).toMatch(/\/batch-delete$/);
    expect(JSON.parse(String(writes[0][1]?.body))).toEqual({ task_ids: ["first", "second"], pending_ids: ["pending"] });
    expect(await screen.findByRole("status")).toHaveTextContent("已删除 1 个文件及关联数据。1 个未删除。");
  });
  it("can cancel a failed task while keeping deletion a separate action",async()=>{
    const item=task("failed","failed");const fetch=server([item]);render(<TaskQueuePage overview={overview([item])}/>);await screen.findByText("failed.pdf");await more("failed.pdf");fireEvent.click(screen.getByRole("menuitem",{name:"取消任务"}));
    await waitFor(()=>expect(fetch.mock.calls.some(([url,init])=>String(url).endsWith("/failed/cancel")&&init?.method==="POST")).toBe(true));expect(fetch.mock.calls.some(([,init])=>init?.method==="DELETE")).toBe(false);
  });
  it("defaults retry to the saved reading scope and offers current settings only in the options dialog",async()=>{
    const item=task("failed","failed");const fetch=server([item]);render(<TaskQueuePage overview={overview([item])}/>);fireEvent.click(await screen.findByRole("button",{name:"重试 failed.pdf"}));await waitFor(()=>expect(fetch.mock.calls.some(([url])=>String(url).endsWith("/failed/retry"))).toBe(true));
    await waitFor(()=>expect(screen.getByRole("button",{name:"更多操作 failed.pdf"})).toBeEnabled());await more("failed.pdf");fireEvent.click(screen.getByRole("menuitem",{name:"重试选项"}));const dialog=await screen.findByRole("dialog",{name:"重试选项"});fireEvent.click(within(dialog).getByRole("radio",{name:"按当前范围重新读取"}));fireEvent.click(within(dialog).getByRole("button",{name:"确认重试"}));
    await waitFor(()=>expect(fetch.mock.calls.some(([url])=>String(url).includes("/failed/retry?use_current_settings=true"))).toBe(true));
  });
  it("retries cancelled files and shows the comparison action only for a retained result", async () => {
    const empty = task("cancelled", "cancelled", { has_result: false });
    const saved = task("saved", "cancelled", { has_result: true });
    const open = vi.fn();
    const fetch = server([empty, saved]);
    render(<TaskQueuePage overview={overview([empty, saved])} onOpenTask={open} />);
    await screen.findByText("cancelled.pdf");
    await more("cancelled.pdf");
    expect(screen.queryByRole("menuitem", { name: "查看原件与结果" })).not.toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
    await more("saved.pdf");
    fireEvent.click(screen.getByRole("menuitem", { name: "查看原件与结果" }));
    await waitFor(() => expect(open).toHaveBeenCalledWith(saved));
    fireEvent.click(screen.getByRole("button", { name: "重试 cancelled.pdf" }));
    await waitFor(() => expect(fetch.mock.calls.some(([url]) => String(url).endsWith("/cancelled/retry"))).toBe(true));
  });
  it("does not call every skipped batch retry a capacity failure",async()=>{
    const item=task("failed","failed");const fetch=server([item],url=>url.pathname.endsWith("/batch-retry")?json({retried:[],skipped:1,skipped_tasks:[{task_id:"failed",reason:"state_changed",message:"状态已变化"}]}):undefined);render(<TaskQueuePage overview={overview([item])}/>);
    fireEvent.click(await screen.findByRole("checkbox",{name:"选择 failed.pdf"}));fireEvent.click(screen.getByRole("button",{name:"重试"}));expect(await screen.findByRole("status")).toHaveTextContent("1 个未重试");expect(screen.getByRole("status")).not.toHaveTextContent("容量");expect(fetch.mock.calls.some(([url])=>String(url).includes("batch-retry"))).toBe(true);
  });
  it("keeps server pagination and search scoped to the selected group",async()=>{
    const items=Array.from({length:51},(_,i)=>task(`done-${i}`,"completed"));const fetch=server(items);render(<TaskQueuePage overview={overview(items)}/>);await screen.findByText("done-0.pdf");fireEvent.click(screen.getByRole("button",{name:"下一页"}));await screen.findByText("done-50.pdf");expect(screen.queryByText("done-0.pdf")).not.toBeInTheDocument();expect(fetch.mock.calls.some(([url])=>String(url).includes("offset=50"))).toBe(true);
    fireEvent.click(screen.getByRole("button",{name:/^已完成/}));await waitFor(()=>expect(fetch.mock.calls.some(([url])=>String(url).includes("group=completed"))).toBe(true));
    fireEvent.change(screen.getByRole("textbox",{name:"搜索任务文件名"}),{target:{value:"done-4"}});await waitFor(()=>expect(fetch.mock.calls.some(([url])=>String(url).includes("search=done-4"))).toBe(true));
  });
  it("retains review, input-scope and export recovery entrypoints without exposing internal IDs",async()=>{
    const items=[task("review-id","needs_review",{has_result:true,filename:"待核对.pdf"}),task("scope-id","waiting_for_template",{filename:"读取范围.pdf",pending_action:"confirm_input_scope",pending_reason:"input_scope"}),task("export-id","completed",{filename:"副本.pdf",file_export:{status:"failed",parent_path:"C:/synthetic",destination:null,actual_path:null,confirmed_name:null,error_code:"destination_exists",error_message:"同名文件"}})];const open=vi.fn();server(items);render(<TaskQueuePage overview={overview(items)} onOpenTask={open}/>);
    fireEvent.click(await screen.findByRole("button",{name:"核对 待核对.pdf"}));expect(open).toHaveBeenCalledWith(items[0]);fireEvent.click(screen.getByRole("button",{name:"确认范围 读取范围.pdf"}));expect(await screen.findByRole("dialog",{name:"确认读取范围"})).toHaveTextContent("仅提取所示范围");fireEvent.click(screen.getByRole("button",{name:"关闭"}));fireEvent.click(screen.getByRole("button",{name:"处理 副本.pdf"}));expect(await screen.findByRole("dialog",{name:"处理外部文件"})).toHaveTextContent("重试副本导出");expect(screen.queryByText("review-id")).not.toBeInTheDocument();
  });
});
