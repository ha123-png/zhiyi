import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { HistoryPage } from "./HistoryPage";
import { DataTablePage } from "./DataTablePage";
vi.mock("./DocumentPreview", () => ({ DocumentPreview: () => null }));

const json = (data: unknown) => new Response(JSON.stringify(data), {status:200, headers:{"Content-Type":"application/json"}});
const task = {id:"drag-history",filename:"合成只读拖拽.txt",status:"completed",content_type:"text/plain",size_bytes:1,
  sha256:"synthetic",template_mode:"smart",template_id:null,template_version:null,candidate_templates:[],
  duplicate_of_task_id:null,created_at:"2026-10-31T00:00:00Z",updated_at:"2026-10-31T00:00:00Z"};

function pointer(target: Element, type: string, x: number, id: number, buttons=1) {
  const event = new MouseEvent(type,{bubbles:true,button:0,buttons,clientX:x});
  Object.defineProperty(event,"pointerId",{value:id});
  fireEvent(target,event);
}

it("regression: a focusable History list separator responds to keyboard resizing", async () => {
  vi.stubGlobal("fetch",vi.fn(async (input: string | URL) => {
    const path=String(input);
    if(path.includes("/templates"))return json([]);
    if(path.includes("/tasks/summary"))return json({total:1,completed:1,needs_review:0,failed:0});
    if(path.includes("/tasks"))return json([task]);
    return json([]);
  }));
  const {container}=render(<HistoryPage />);
  await screen.findByText(task.filename);
  const handle=screen.getByRole("separator",{name:"调整第 2 列宽"});
  const column=container.querySelectorAll("colgroup col")[1] as HTMLElement;
  handle.focus();
  expect(handle).toHaveFocus();
  expect(column.style.width).toBe("240px");
  fireEvent.keyDown(handle,{key:"ArrowRight"});
  expect(column.style.width).not.toBe("240px");
});

async function dataDivider(){
  const table={id:"drag-table",name:"合成只读表",template_key:"drag-tpl",template_version:"1",document_kind:"custom",row_count:1,created_at:"2026-10-31T00:00:00Z"};
  vi.stubGlobal("fetch",vi.fn(async (input:string|URL) => {
    const path=String(input);
    if(path.endsWith("/tables"))return json([table]);
    if(path.includes("/views")||path.includes("/templates"))return json([]);
    return json({...table,columns:[{key:"name",label:"名称",value_type:"text",section:"item"}],
      page:1,page_size:10,rows:[{id:1,task_id:null,item_index:1,version:1,values:{name:"合成名称"},created_at:"",updated_at:""}]});
  }));
  render(<DataTablePage />);
  const divider=await screen.findByRole("separator",{name:"调整数据表列表宽度"});
  Object.assign(divider,{setPointerCapture:vi.fn(),hasPointerCapture:()=>true,releasePointerCapture:vi.fn()});
  return divider;
}

it("regression: DataTable tree divider stops on window blur",async()=>{
  const divider=await dataDivider();
  pointer(divider,"pointerdown",100,7);
  pointer(divider,"pointermove",120,7);
  expect(divider).toHaveAttribute("aria-valuenow","260");
  fireEvent.blur(window);
  pointer(divider,"pointermove",170,7,0);
  expect(divider).toHaveAttribute("aria-valuenow","260");
});

it("regression: DataTable tree divider ignores movement from another pointer",async()=>{
  const divider=await dataDivider();
  pointer(divider,"pointerdown",100,7);
  pointer(divider,"pointermove",120,7);
  expect(divider).toHaveAttribute("aria-valuenow","260");
  pointer(divider,"pointermove",170,8);
  expect(divider).toHaveAttribute("aria-valuenow","260");
});

it("regression: History detail separator supports keyboard and both width boundaries", async () => {
  const result = { header: {}, items: [{product_name:"合成明细",quantity:2}] };
  vi.stubGlobal("fetch",vi.fn(async(input:string|URL)=>{
    const path=String(input);
    if(path.includes("/templates"))return json([]);
    if(path.includes("/tasks/summary"))return json({total:1,completed:1,needs_review:0,failed:0});
    if(path.endsWith("/result"))return json({task_id:task.id,review_version:1,result,original_result:result,
      detail_ids:[77],validation_issues:[],current_schema:[
        {key:"product_name",label:"货品",section:"item",value_type:"text"},
        {key:"quantity",label:"数量",section:"item",value_type:"number"}]});
    if(path.includes("/tasks"))return json([task]);
    return json([]);
  }));
  render(<HistoryPage />);
  fireEvent.click(await screen.findByText(task.filename));
  const handle=await screen.findByRole("separator",{name:"调整“货品”列宽"});
  handle.focus();
  expect(handle).toHaveFocus();
  expect(handle).toHaveAttribute("aria-valuemin","64");
  expect(handle).toHaveAttribute("aria-valuemax","640");
  expect(handle).toHaveAttribute("aria-valuenow","132");
  fireEvent.keyDown(handle,{key:"End"});
  expect(handle).toHaveAttribute("aria-valuenow","640");
  fireEvent.keyDown(handle,{key:"ArrowRight"});
  expect(handle).toHaveAttribute("aria-valuenow","640");
  fireEvent.keyDown(handle,{key:"Home"});
  expect(handle).toHaveAttribute("aria-valuenow","64");
  fireEvent.keyDown(handle,{key:"ArrowLeft"});
  expect(handle).toHaveAttribute("aria-valuenow","64");
  fireEvent.keyDown(handle,{key:"ArrowRight"});
  expect(handle).toHaveAttribute("aria-valuenow","76");
});
