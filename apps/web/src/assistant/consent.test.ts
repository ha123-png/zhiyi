import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { consentKey, readServiceConsentState, revokeProfileGrants, saveServiceConsent, serviceConsentKey, serviceConsentStorageKey, sessionConsentRevision } from "./consent";
beforeEach(() => { localStorage.clear(); sessionStorage.clear(); });
afterEach(() => vi.restoreAllMocks());
const profile = {id:"p", version:1, base_url:"https://example.test/v1"};
it("binds consent to conversation, provider version and data scope", () => {
  const key = consentKey("a", profile, {table_ids:["one"]});
  expect(consentKey("b", profile, {table_ids:["one"]})).not.toBe(key);
  expect(consentKey("a", {...profile, version:2}, {table_ids:["one"]})).not.toBe(key);
  expect(consentKey("a", {...profile, base_url:"https://other.test"}, {table_ids:["one"]})).not.toBe(key);
  expect(consentKey("a", profile, {workspace:true})).not.toBe(key);
  expect(consentKey("a", profile, {table_ids:["one"], row_ids:[]})).not.toBe(key);
  expect(consentKey("a", profile, {table_id:"one", source_keys:[]})).not.toBe(consentKey("a", profile, {table_id:"one"}));
  expect(consentKey("a", profile, {table_id:"one", source_keys:['{"id":"A","kind":"file"}']})).not.toBe(consentKey("a", profile, {table_id:"one", source_keys:['{"id":"B","kind":"file"}']}));
});
it("ignores permission-mode downgrade and order without widening data", () => {
  expect(consentKey("a", profile, {table_ids:["b","a"], mode:"delegate"}))
    .toBe(consentKey("a", profile, {table_ids:["a","b"], mode:"assist"}));
});

const service = { ...profile, provider: "openai_compatible", model_name: "document-model" };
const storageKey = serviceConsentStorageKey("/api/v1/assistant");

it("remembers only the same profile, provider, endpoint path and model target", () => {
  const key = serviceConsentKey(service);
  expect(serviceConsentKey({ ...service, version: 2 })).toBe(key);
  expect(serviceConsentKey({ ...service, base_url: "https://EXAMPLE.test:443/v1/" })).toBe(key);
  for (const changed of [
    { id: "other" }, { provider: "ollama" }, { model_name: "other-model" },
    { base_url: "https://other.test/v1" }, { base_url: "https://example.test/other-tenant/v1" },
    { base_url: "http://example.test/v1" }, { base_url: "https://example.test:8443/v1" },
  ]) expect(serviceConsentKey({ ...service, ...changed })).not.toBe(key);
  expect(serviceConsentStorageKey("http://127.0.0.1:8818/api/v1/assistant")).not.toBe(storageKey);
});

it("persists explicit service approval across reads and removes it on revocation", () => {
  const key = serviceConsentKey(service);
  expect(readServiceConsentState(storageKey).keys).toEqual([]);
  expect(localStorage.getItem(storageKey)).toBeNull();
  expect(saveServiceConsent(storageKey, key, true).saved).toBe(true);
  expect(readServiceConsentState(storageKey).keys).toEqual([key]);
  expect(saveServiceConsent(storageKey, key, false)).toMatchObject({ saved: true, keys: [] });
  expect(readServiceConsentState(storageKey).keys).toEqual([]);
});

it("never stores credentials from endpoint userinfo, query or fragments", () => {
  for (const base_url of ["https://user:secret@example.test/v1", "https://example.test/v1?api_key=secret", "https://example.test/v1#secret"]) {
    const unsafe = { ...service, base_url };
    expect(serviceConsentKey(unsafe)).toBe("");
    expect(consentKey("thread", unsafe, {})).toBe("");
    expect(saveServiceConsent(storageKey, serviceConsentKey(unsafe), true).saved).toBe(false);
  }
  expect(localStorage.getItem(storageKey)).toBeNull();
  localStorage.setItem(storageKey, JSON.stringify([JSON.stringify(["p", "provider", "https://example.test?key=secret", "model"]), {}, 2]));
  expect(readServiceConsentState(storageKey).keys).toEqual([]);
});

it("revokes all scoped and legacy session grants for the profile without touching other services", () => {
  const other = consentKey("other-thread", { ...service, id: "other" }, {});
  const grants = [consentKey("one", service, {}), consentKey("two", service, { table_id: "t", row_ids: [1] }),
    JSON.stringify(["legacy", service.id, 1, service.base_url, {}]), other];
  expect(revokeProfileGrants(grants, service.id)).toEqual([other]);
});

it("fails closed for malformed or unavailable storage without preventing temporary approval", () => {
  localStorage.setItem(storageKey, "not-json");
  expect(readServiceConsentState(storageKey).keys).toEqual([]);
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new DOMException("blocked", "SecurityError"); });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("blocked", "SecurityError"); });
  expect(readServiceConsentState(storageKey).keys).toEqual([]);
  expect(saveServiceConsent(storageKey, serviceConsentKey(service), true)).toMatchObject({ saved: false, keys: [] });
  expect(consentKey("current", service, {})).not.toBe("");
});

it("invalidates dormant session approvals when a different window revoked the same profile", () => {
  const key = serviceConsentKey(service);
  const allowed = saveServiceConsent(storageKey, key, true, false);
  expect(allowed.keys).toEqual([]);
  const before = sessionConsentRevision(allowed.state, service.id);
  expect(before).not.toBe("");
  const oldGrant = consentKey("thread", service, { table_id: "t" }, before);
  saveServiceConsent(storageKey, key, false);
  const after = sessionConsentRevision(readServiceConsentState(storageKey), service.id);
  expect(consentKey("thread", service, { table_id: "t" }, after)).not.toBe(oldGrant);
  expect(readServiceConsentState(storageKey).keys).toEqual([]);
});

it("clears old persistent approvals when a quota error prevents writing the revocation", () => {
  const key = serviceConsentKey(service);
  saveServiceConsent(storageKey, key, true);
  const before = sessionConsentRevision(readServiceConsentState(storageKey), service.id);
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("full", "QuotaExceededError"); });
  expect(saveServiceConsent(storageKey, key, false)).toMatchObject({ saved: true, keys: [] });
  expect(readServiceConsentState(storageKey).keys).toEqual([]);
  expect(sessionConsentRevision(readServiceConsentState(storageKey), service.id)).not.toBe(before);
});

it("retains a reload-safe window revocation when local storage rejects both writing and removal", () => {
  const key = serviceConsentKey(service);
  saveServiceConsent(storageKey, key, true);
  const set = Storage.prototype.setItem;
  const remove = Storage.prototype.removeItem;
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, name, value) {
    if (this === localStorage) throw new DOMException("blocked", "SecurityError");
    set.call(this, name, value);
  });
  vi.spyOn(Storage.prototype, "removeItem").mockImplementation(function (this: Storage, name) {
    if (this === localStorage) throw new DOMException("blocked", "SecurityError");
    remove.call(this, name);
  });
  expect(saveServiceConsent(storageKey, key, false)).toMatchObject({ saved: false, sessionSaved: true, keys: [] });
  expect(JSON.parse(localStorage.getItem(storageKey)!).keys).toEqual([key]);
  expect(readServiceConsentState(storageKey).keys).toEqual([]);
  vi.restoreAllMocks();
  expect(readServiceConsentState(storageKey).keys).toEqual([]);
  saveServiceConsent(storageKey, key, true);
  expect(readServiceConsentState(storageKey).keys).toEqual([key]);
});

it("reports the unavoidable persistence limit when both storage areas reject every change", () => {
  const key = serviceConsentKey(service);
  saveServiceConsent(storageKey, key, true);
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("blocked", "SecurityError"); });
  vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => { throw new DOMException("blocked", "SecurityError"); });
  expect(saveServiceConsent(storageKey, key, false)).toMatchObject({ saved: false, sessionSaved: false, keys: [] });
});
