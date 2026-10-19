import { apiErrorMessage } from "./api";

describe("apiErrorMessage", () => {
  test("mostra il dettaglio strutturato degli errori bulk", () => {
    const error = {
      response: {
        data: {
          detail: {
            message: "Caricamento rifiutato: nessuna vendita registrata",
            errori: [
              { riga: 2, errore: "prodotto non trovato: 12345" },
              { codice: "67890", errore: "giacenza 1, richiesti 3" },
            ],
          },
        },
      },
    };

    expect(apiErrorMessage(error, "Errore bulk")).toBe(
      "Caricamento rifiutato: nessuna vendita registrata · "
      + "Riga 2: prodotto non trovato: 12345 · "
      + "Codice 67890: giacenza 1, richiesti 3"
    );
  });

  test("mantiene il fallback per un dettaglio oggetto vuoto", () => {
    expect(apiErrorMessage({ response: { data: { detail: {} } } }, "Errore bulk"))
      .toBe("Errore bulk");
  });
});
