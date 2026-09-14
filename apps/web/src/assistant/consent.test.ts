import { expect, it } from "vitest";
import { consentKey } from "./consent";
const profile = {id:"p", version:1, base_url:"https://example.test/v1"};
it("binds consent to conversation, provider version and data scope", () => {
  const key = consentKey("a", profile, {table_ids:["one"]});
  expect(consentKey("b", profile, {table_ids:["one"]})).not.toBe(key);
  expect(consentKey("a", {...profile, version:2}, {table_ids:["one"]})).not.toBe(key);
  expect(consentKey("a", {...profile, base_url:"https://other.test"}, {table_ids:["one"]})).not.toBe(key);
  expect(consentKey("a", profile, {workspace:true})).not.toBe(key);
  expect(consentKey("a", profile, {table_ids:["one"], row_ids:[]})).not.toBe(key);
});
it("ignores permission-mode downgrade and order without widening data", () => {
  expect(consentKey("a", profile, {table_ids:["b","a"], mode:"delegate"}))
    .toBe(consentKey("a", profile, {table_ids:["a","b"], mode:"assist"}));
});
