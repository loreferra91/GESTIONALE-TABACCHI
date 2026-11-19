import { act } from "react";
import { createRoot } from "react-dom/client";
jest.mock("../components/Layout", () => ({ children }) => <main>{children}</main>);
jest.mock("../lib/api", () => ({ api: { get: jest.fn(), post: jest.fn() }, apiErrorMessage: (_e, fallback) => fallback }));
jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn(), info: jest.fn() } }));
import Vendite from "./Vendite";
import { api } from "../lib/api";
let container, root;
beforeEach(async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  Object.defineProperty(global, 'crypto', { configurable: true, value: { randomUUID: jest.fn(() => 'test-key-123456789') } });
  api.get.mockImplementation(path => Promise.resolve({ data:
    path === '/vendite/totali' ? { importo: 1503, pezzi: 1002, righe: 501 } :
    path === '/prodotti' ? [{ codice: 'P1', prezzo: 5 }] :
    path.endsWith('/ultimo') || path.endsWith('/ultima') ? null : []
  }));
  container = document.createElement('div'); document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => root.render(<Vendite />));
  await act(async () => [...container.querySelectorAll('button')].find(b => b.textContent === 'Bulk paste da Excel').click());
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); jest.clearAllMocks(); delete global.IS_REACT_ACT_ENVIRONMENT; });
async function paste(text) {
  const input = container.querySelector('[data-testid="bulk-text"]');
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(input, text);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}
test('blocca interamente un caricamento con quantità frazionaria', async () => {
  await paste('P1\tProdotto\t2\t10\nP1\tProdotto\t1,5\t5');
  await act(async () => container.querySelector('[data-testid="bulk-submit"]').click());
  expect(api.post).not.toHaveBeenCalled();
});
test('riutilizza la chiave dopo timeout e converte 2.0 in 2', async () => {
  api.post.mockRejectedValueOnce(new Error('timeout')).mockResolvedValueOnce({ data: { inseriti: 1, saltati: 0, batch_id: 'batch-1' } });
  await paste('P1\tProdotto\t2.0\t10');
  await act(async () => container.querySelector('[data-testid="bulk-submit"]').click());
  await act(async () => container.querySelector('[data-testid="bulk-submit"]').click());
  expect(api.post).toHaveBeenCalledTimes(2);
  expect(api.post.mock.calls[0][1]).toEqual(api.post.mock.calls[1][1]);
  expect(api.post.mock.calls[0][1].righe[0].quantita).toBe(2);
  expect(api.post.mock.calls[0][1].idempotency_key).toBeTruthy();
});
test('mostra il conteggio completo indipendente dalla lista', () => {
  expect(container.textContent).toContain('Mostrate 0 di 501 vendite');
});
