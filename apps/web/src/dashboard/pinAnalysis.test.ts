import { expect, it } from "vitest";
import type { Analysis } from "../assistant/types";
import { statisticFromAnalysis } from "./pinAnalysis";

const base: Analysis = { analysis_id:"a", truncated:false, warnings:[], metric_keys:["sum:amount"], source: { table_id:"table", table_name:"收支", row_count:2, document_count:2, grain:"row", generated_at:"2026-09-13", request:{ table_id:"table", dimensions:["date"], metrics:[{op:"sum",field:"amount"}], filters:[], time_bucket:"month", grain:"auto" } } };
it("maps a simple query without changing its source or time scope", () => {
  expect(statisticFromAnalysis(base)).toMatchObject({table_id:"table", metric:"sum", metric_field:"amount", group_field:"date", time_bucket:"month", time_range:"all", display:"line"});
});
it("does not turn a restricted snapshot into a wider live statistic", () => {
  for (const restriction of [{filters:[{field:"vendor",op:"eq",value:"A"}]},{row_ids:[]},{row_ids:[1]},{task_id:"x"},{search:"限定"},{grain:"document"},{time_bucket:"year"},{metrics:[{op:"count",field:"amount"}]},{dimensions:["date","vendor"]}]) {
    expect(statisticFromAnalysis({...base,source:{...base.source,request:{...base.source.request,...restriction}}})).toBeNull();
  }
  expect(statisticFromAnalysis({...base,truncated:true})).toBeNull();
});
