import { act } from "react";
import { createRoot } from "react-dom/client";

jest.mock("../components/Layout", () => ({ children }) => <main>{children}</main>);
jest.mock("../lib/api", () => ({
  API: "/api",
  api: { get: jest.fn(), post: jest.fn(), put: jest.fn() },
  apiErrorMessage: (error, fallback) => error?.response?.data?.detail || fallback,
}));
jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn(), info: jest.fn() },
}));

import Parametri from "./Parametri";
import { api } from "../lib/api";
import { toast } from "sonner";

let container;
let root;

beforeEach(async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  api.get.mockImplementation(path => Promise.resolve({
    data: path === "/backup/restore-file/status" ? { phase: "READY" } : [],
  }));
  api.post.mockImplementation(path => {
    if (path === "/backup/restore-file/preview") {
      return Promise.resolve({
        data: {
          can_restore: true,
          total_docs: 2,
          counts: { prodotti: 1, vendite: 1 },
          warnings: [],
          nonempty_missing_collections: [],
        },
      });
    }
    if (path === "/backup/restore-file") {
      return Promise.resolve({ data: { restored_docs: 2 } });
    }
    return Promise.reject(new Error(`Unexpected POST ${path}`));
  });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => root.render(<Parametri />));
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  jest.clearAllMocks();
  delete global.IS_REACT_ACT_ENVIRONMENT;
});

async function chooseRestoreFile(file) {
  const input = container.querySelector('[data-testid="backup-restore-file"]');
  Object.defineProperty(input, "files", { configurable: true, value: [file] });
  await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));
}

test("previews external JSON before enabling destructive restore", async () => {
  await chooseRestoreFile(new File(["{}"], "gestionale-backup-fixture.json", { type: "application/json" }));

  expect(api.post).toHaveBeenCalledTimes(1);
  expect(api.post.mock.calls[0][0]).toBe("/backup/restore-file/preview");
  expect(container.textContent).toContain("2 documenti in 2 collezioni");
  expect(container.textContent).toContain("Ripristinare il backup dal file?");

  const confirm = [...container.querySelectorAll("button")].find(
    button => button.textContent === "Ripristina dal file",
  );
  await act(async () => confirm.click());

  expect(api.post).toHaveBeenCalledTimes(2);
  expect(api.post.mock.calls[1][0]).toBe("/backup/restore-file");
  expect(toast.success).toHaveBeenCalled();
});

test("blocks restore when preview finds populated missing ledger", async () => {
  api.post.mockResolvedValueOnce({
    data: {
      can_restore: false,
      total_docs: 2,
      counts: { prodotti: 1 },
      warnings: ["Incomplete legacy backup"],
      nonempty_missing_collections: ["vending_operations"],
    },
  });
  await chooseRestoreFile(new File(["{}"], "legacy-backup.json", { type: "application/json" }));

  expect(container.textContent).toContain("Ripristino bloccato");
  expect(container.textContent).toContain("vending_operations");
  expect(container.textContent).not.toContain("Ripristinare il backup dal file?");
  expect(api.post).toHaveBeenCalledTimes(1);
  expect(toast.error).toHaveBeenCalled();
});

test("rejects oversized file in browser without network or DB writes", async () => {
  const tooLarge = new File(["a"], "large.json", { type: "application/json" });
  Object.defineProperty(tooLarge, "size", { configurable: true, value: 20 * 1024 * 1024 + 1 });

  await chooseRestoreFile(tooLarge);

  expect(api.post).not.toHaveBeenCalled();
  expect(toast.error).toHaveBeenCalledWith(expect.stringContaining("20 MiB"));
  expect(container.textContent).not.toContain("Ripristinare il backup dal file?");
});


test("blocks a new upload while a staging restore is incomplete", async () => {
  api.get.mockImplementation(path => Promise.resolve({
    data: path === "/backup/restore-file/status"
      ? { phase: "RESTORING", checkpoint_id: "cert-synthetic" } : [],
  }));
  await chooseRestoreFile(new File(["{}"], "gestionale-backup-test.json", { type: "application/json" }));
  expect(api.post).not.toHaveBeenCalled();
  expect(toast.error).toHaveBeenCalledWith(expect.stringContaining("Ripristino precedente incompleto"));
  expect(container.querySelector('[data-testid="backup-restore-status-warning"]').textContent)
    .toContain("RESTORING");
});

test("rechecks restore state immediately before destructive POST", async () => {
  await chooseRestoreFile(new File(["{}"], "gestionale-backup-test.json", { type: "application/json" }));
  api.get.mockImplementation(path => Promise.resolve({
    data: path === "/backup/restore-file/status" ? { phase: "FAILED" } : [],
  }));
  const confirm = [...container.querySelectorAll("button")].find(
    button => button.textContent === "Ripristina dal file",
  );
  await act(async () => confirm.click());
  expect(api.post.mock.calls.map(call => call[0])).toEqual(["/backup/restore-file/preview"]);
  expect(toast.error).toHaveBeenCalledWith(expect.stringContaining("staging non è nello stato READY"));
  expect(container.querySelector('[data-testid="backup-restore-status-warning"]').textContent)
    .toContain("FAILED");
});

test("HTTP 503 with READY state reports checkpoint recovery but never retries automatically", async () => {
  await chooseRestoreFile(new File(["{}"], "gestionale-backup-test.json", { type: "application/json" }));
  api.post.mockImplementation(path => {
    if (path === "/backup/restore-file") {
      return Promise.reject({ response: { status: 503, data: { detail: "Rollback verified" } } });
    }
    return Promise.reject(new Error("Unexpected POST"));
  });
  const confirm = [...container.querySelectorAll("button")].find(
    button => button.textContent === "Ripristina dal file",
  );
  await act(async () => confirm.click());
  expect(api.post.mock.calls.filter(call => call[0] === "/backup/restore-file")).toHaveLength(1);
  expect(toast.error).toHaveBeenCalledWith(expect.stringContaining("checkpoint precedente recuperato"));
});

test("status endpoint failure blocks a fresh restore preview", async () => {
  api.get.mockImplementation(path => path === "/backup/restore-file/status"
    ? Promise.reject({ response: { status: 503 } }) : Promise.resolve({ data: [] }));
  await chooseRestoreFile(new File(["{}"], "gestionale-backup-test.json", { type: "application/json" }));
  expect(api.post).not.toHaveBeenCalled();
  expect(container.querySelector('[data-testid="backup-restore-status-warning"]').textContent)
    .toContain("UNKNOWN");
});
