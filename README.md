
# Webshot & Quote Extractor (v3)

Cosa fa:
- Naviga una lista di URL
- Scatta screenshot full-page (timestamp folder)
- Estrae **Nome, Prezzo, Variazione %, Data/Ora** quando possibile
- Salva `quotes.csv` e `quotes.json`
- (Opzionale) **committa** gli output nel repo

## Input
Puoi usare `urls.csv` (consigliato) con colonne:
- `url` (obbligatorio)
- `name_sel`, `price_sel`, `change_sel`, `datetime_sel` (CSS opzionali)

Oppure `urls.txt` con un URL per riga (in tal caso verranno usate regole predefinite per Investing).

## Avvio
1. Aggiungi i file al repo.
2. Modifica `urls.csv` con i tuoi link (e, se vuoi, i selettori CSS).
3. In GitHub → **Actions** → avvia **Webshot & Quote Extractor (v3)**.
4. Gli output vanno in `webshots/YYYYMMDD_HHMMSS/`:
   - `*.png` (screenshot)
   - `quotes.csv` / `quotes.json` (tabella estratta)

## Parametri
- `input_file`: default `urls.csv`
- `out_dir`: default `webshots`
- `viewport`: default `1366x768`
- `delay_ms`: default `1500`
- `commit_outputs`: default `yes` (committa gli output nel repo)

## Note
- Il job tenta di chiudere i cookie banner più comuni.
- Per siti non-compatibili, puoi passare **selettori CSS** in `urls.csv` per forzare l'estrazione.
- Per Investing.com, se presenti, verranno letti anche i **frammenti di link** (`#:~:text=...`) per estrarre prezzo e variazione.
