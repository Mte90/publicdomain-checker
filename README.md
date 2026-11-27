# Public Domain Checker for Wiki Commons

Un'Agent Skill che decide se materiale scannerizzato (fotografie, cartoline, lettere, santini, ecc) è di pubblico dominio, e prepara i metadati necessari per pubblicarlo su Wikimedia Commons e Wikidata.

Legge una cartella di materiale e un'estrazione leggibile dalla macchina di cosa sia e cosa dica ciascun elemento. Applica in modo deterministico le regole di copyright per Italia, Regno Unito e Vaticano, e scrive un unico file JSON portabile per cartella più una dashboard HTML autonoma per la revisione umana.

Ogni decisione è spiegabile: lo strumento indica la base legale, la versione delle tabelle regole e l'affidabilità. Non inventa mai un identificativo, un template di licenza o una categoria: ciascuno è convalidato su un catalogo Wikimedia reale prima di essere scritto.

Gli internals del pacchetto sono descritti in [`pd_checker/README.md`](pd_checker/README.md). La documentazione per agenti, [`pd_checker/SKILL.md`](pd_checker/SKILL.md), è volontariamente in inglese.

Esempio di dashboard generata da questo progetto: [`dashboard.html`](dashboard.html) (senza le immagini).

Il codice e i metadati generati (pubdom.json, dashboard, dichiarazioni Wikidata) sono rilasciati con **CC0 1.0 Universal** — vedi [LICENSE](LICENSE): riusali liberamente, anche commercialmente, senza chiedere il permesso.

## Cosa non è

- Non è consulenza legale. Il verdetto è deterministico e documentato; la decisione finale di pubblicare è tua.
- Non è autonomo. Una persona rivede la dashboard (file generato) ed esegue ogni caricamento.

## Percorso normale: con l'agente

1. **Prepara la cartella di scansioni.** Metti le scansioni in una cartella del progetto, per esempio `Concorsi/`. Non prepari nessun altro file: la cartella di scansioni è l'unico input.

2. **Chiedi all'agente.** Apri l'agente nella cartella del progetto e chiedi: «Analizza la cartella Concorsi seguendo pd_checker/SKILL.md». Il file [`pd_checker/SKILL.md`](pd_checker/SKILL.md) contiene le istruzioni complete per l'agente, in inglese; indicandolo nel prompt il percorso funziona con qualsiasi agente capace, anche senza auto-scoperta della skill.

3. **L'agente legge le scansioni, il motore decide.** L'agente legge ogni scansione con il suo modello a visione e scrive l'estrazione dei fatti in `Concorsi/pd_data/facts.json`: tipo di documento, date, nomi, testi, classificazione. Poi esegue il motore `pd_checker`, che applica in modo deterministico le regole di copyright. Il modello estrae i fatti; il motore decide la legge. Tu non prepari nulla oltre la cartella: `facts.json` lo produce il passo di estrazione dell'agente.

4. **Rivedi la dashboard.** L'agente produce `Concorsi/pubdom.json` e `Concorsi/dashboard.html`. Apri `Concorsi/dashboard.html` nel browser: per ogni elemento trovi il verdetto, l'affidabilità, la base legale, l'evidenza della data, i metadati Wikimedia proposti e le miniature. Se l'agente lavora in un ambiente remoto, scarica prima i file dalla cartella del progetto.

5. **Se una persona o un editore del pezzo non è su Wikidata**, la dashboard te lo dice nella lista dettagli («Assente su Wikidata»): puoi lasciare così — il nome resta comunque nel wikitext della pagina file — oppure creare l'item tu e sistemare tutto con un solo comando: vedi «Completare le entità mancanti» nel blocco caricamento qui sotto.

6. **Carica quando gli elementi sono *Pronto*.** Il caricamento parte sempre in dry-run: `push` mostra cosa invierebbe, senza rete e senza credenziali. Prima dell'invio, `preflight` verifica il pacchetto (duplicati su Commons, template e categorie) e segnala ogni blocco con il motivo. L'invio vero richiede `--live` e le tue credenziali Wikimedia, un token OAuth owner-only o una bot password: la procedura completa per crearle è nel blocco sviluppatori qui sotto. I file già esistenti su Commons non vengono mai sovrascritti. Dopo l'upload (e sui file dell'archivio già presenti), `push` aggancia anche i dati strutturati (SDC) del pacchetto: solo i claim mancanti, mai duplicati.

## Come si decide il verdetto

Il motore valuta il paese d'origine e l'Italia applicando la regola del termine più breve: pubblico dominio solo se lo è in entrambi. I template di licenza descrivono sempre il paese d'origine, quindi un'opera non italiana non riceve mai un template italiano.

Ogni verdetto riporta l'affidabilità, la base legale e il motivo. Dati mancanti e vincoli non-copyright sono indicati separatamente. Quando il verdetto è `incerto` non viene emesso alcun template di licenza.

Le regole operative, in sintesi (i termini decorrono dal 1° gennaio dell'anno successivo, Art. 32-ter):

| Materiale | Regola | Template |
| --- | --- | --- |
| Foto semplice scattata ≤2004 | ventennale già scaduto → pubblico dominio, affidabilità media (la riforma L. 182/2025 non risuscita i termini scaduti) | `{{PD-Italy}}` + `{{PD-1996}}` |
| Foto semplice scattata ≥2005 | protetta 70 anni dalla produzione | — |
| Opera artistica, autore noto | 70 anni dalla morte dell'autore (Art. 25) | `{{PD-old-70}}` + `{{PD-1996}}` |
| Opera artistica anonima | 70 anni dalla prima pubblicazione (Art. 27) | `{{PD-anon-70-EU}}` + `{{PD-1996}}` |
| Lettera privata | copyright vita+70, ma il consenso dell'Art. 93 non scade: sempre `incerto` | — |

Un anno stimato che cada a meno di dieci anni da una scadenza non decide: il verdetto resta `incerto`. Un'opera il cui termine era già scaduto alla data URAA (1° gennaio 1996) è PD anche negli USA (`{{PD-1996}}`); le fotografie britanniche anonime pre-1957 lo erano solo se realizzate entro il 1925 (revival UK del 1995, reg. 16(d)) — le altre restano sotto copyright USA fino a 95 anni dalla pubblicazione.

Il Vaticano segue 70 anni (L. CXCVII/2017) e non ha template di paese su Commons: si usano i tag generici europei.

Fonti primarie: L. 633/1941 · L. 182/2025 art. 47 · Direttive 93/98/CEE e 2006/116/CE · Convenzione di Berna · 17 U.S.C. §104A (URAA) · CDPA 1988 s.12 ([legislation.gov.uk](https://www.legislation.gov.uk/ukpga/1988/48/section/12)) · Copyright Act 1956 s.3(4)(b) · SI 1995/3297 regg. 5 e 16 ([legislation.gov.uk](https://www.legislation.gov.uk/uksi/1995/3297/regulation/16)). Nessun verdetto è consulenza legale: la decisione di caricare resta tua.

## Limiti

Il verdetto è tanto buono quanto l'estrazione: un errore di lettura (una data, un nome) si propaga al verdetto, per questo la dashboard mostra l'evidenza della data da verificare. Un nome assente dalla cache non riceve QID e resta `da_rivedere`. I termini UK e USA sono verificati sulle fonti primarie e fissati nel motore; i dati strutturati su Commons li aggancia `push` dopo l'upload (solo i claim mancanti, idempotente).

## Percorso da riga di comando / per sviluppatori

Tutto ciò che l'agente fa è eseguibile a mano. L'unico input esterno è la cartella di scansioni; l'estrazione `pd_data/facts.json` la produce il passo di estrazione (l'agente con il modello a visione) seguendo lo schema in [`pd_checker/SKILL.md`](pd_checker/SKILL.md).

### Requisiti

- Python 3.10 o più recente.
- Nessuna dipendenza di terze parti: solo la libreria standard, per scelta.

Esecuzione da un checkout:

```bash
git clone https://github.com/Mte90/publicdomain-checker
cd publicdomain-checker
python3 -m pd_checker --help
```

Il pacchetto si può anche installare con `pip install .`, che fornisce il comando `pd-checker`.

### Comandi

Sulla cartella di esempio `Concorsi/`:

```bash
# regole -> metadati -> correlazione -> dashboard -> pubdom.json
python3 -m pd_checker run Concorsi

# riepilogo su una schermata
python3 -m pd_checker status Concorsi

# convalida template e identificativi (offline)
python3 -m pd_checker validate Concorsi

# controllo del pacchetto di caricamento (--offline salta le verifiche di rete)
python3 -m pd_checker preflight Concorsi --offline

# crea entità Wikidata per entities_to_create (--live per l'invio, source_url obbligatorio)
python3 -m pd_checker create-entity Concorsi

# trascrive le scansioni col modello OCR di regolo (--apply scrive ocr_texts nel pacchetto)
export PD_OCR_API_KEY='…'   # oppure REGOLOAI_OPENCODE_API_KEY
python3 -m pd_checker ocr Concorsi --apply
```

### Trascrizione OCR (`ocr`)

L'estrazione del passo visione trascrive ciò che il modello legge; quando il testo è fitto, piccolo o in parte illegibile, il passo OCR può completarlo. Il comando chiama un endpoint **OpenAI-compatibile** (chat completions con immagine inline) e non è legato a un fornitore: si configura via ambiente e funziona con qualsiasi fornitore compatibile:

- `PD_OCR_API_KEY` — chiave API (fallback: `REGOLOAI_OPENCODE_API_KEY`)
- `PD_OCR_BASE_URL` — predefinito `https://api.regolo.ai/v1`
- `PD_OCR_MODEL` — predefinito `deepseek-ocr-2`

Con `--apply` ogni lato finisce nella mappa `ocr_texts` dell'item in facts.json (chiave = nome file, idempotente); la dashboard lo mostra in un pannello separato accanto alla trascrizione visione. Le trascrizioni OCR sono **materiale di revisione, mai fatti legali**: un anno trovato solo dall'OCR lo conferma la persona (`reference_year` + `date_evidence`). Verifica sempre l'output prima di fidarti: un OCR può allucinare dettagli (nomi, date) — se lo fa, non applicarlo (`--apply` resta facoltativo, e senza flag il comando stampa e basta).

Opzioni utili per `run`:

```bash
python3 -m pd_checker run <cartella> --upload-policy strict   # predefinito
python3 -m pd_checker run <cartella> --upload-policy pd-only  # basta il pubblico dominio
python3 -m pd_checker run <cartella> --upload-policy all     # tutto pronto, anche gli incerti
python3 -m pd_checker run <cartella> --images-base .          # base per le miniature
```

`--upload-policy` decide quando un elemento viene marcato *Pronto*:

- `strict` (predefinito): nessun blocco non-copyright (diritti sull'immagine, privacy) e nessun dato mancante;
- `pd-only`: basta il pubblico dominio con affidabilità alta;
- `all`: ogni elemento è pronto, anche quelli con verdetto incerto. È una tua esplicita assunzione di responsabilità: il motivo resta scritto su ciascun elemento, e gli elementi incerti continuano a non ricevere alcun template di licenza PD.

### Cosa produce lo strumento

- `pubdom.json` — un unico file portabile per cartella: contesto, manifest dei modelli, verdetto di ogni elemento, correlazioni tra serie e manifest di caricamento. Leggibile da solo; il file da consegnare a chi revisiona o da archiviare.
- `dashboard.html` — una pagina statica (interfaccia in italiano; unico script: il tasto ESC chiude l'ingrandimento delle immagini) con il riepilogo dei verdetti, la scheda di ogni elemento, l'evidenza della data, i metadati Wikimedia proposti e le miniature cliccabili.
- I file intermedi vivono in `<cartella>/pd_data/` e sono scratch. `facts.json` è l'eccezione: è l'estrazione scritta dal passo di estrazione dell'agente, non un file da preparare a mano.

### Livelli di convalida

Nulla di ciò che esce dallo strumento è inventato:

| Livello | Catalogo | Comando |
| --- | --- | --- |
| Template di licenza | `commons_templates.json` | `validate`, `verify-live` |
| Entità Wikidata | `wikidata_entities.json` | `validate` |
| Nome → QID | `wd_cache.json` | `resolve` |
| Categorie Commons | `cat_cache.json` | `preflight` |

Un QID è l'identificativo numerico di un'entità Wikidata — persone, luoghi, concetti (es. `Q507540` è Rafael Merry del Val). La risoluzione nome→QID attinge solo da `wd_cache.json`: un nome che non è in cache non riceve nessun QID e resta marcato da rivedere.

Aggiorna i cataloghi scaricati quando Wikimedia li cambia:

```bash
python3 -m pd_checker update                  # template + entità
python3 -m pd_checker templates-enrich        # metadati delle categorie
python3 -m pd_checker verify-live <cartella> # verifica live dei template
```

Un template che non esiste, un identificativo sconosciuto o una categoria non verificabile vengono segnalati sull'elemento, mai scritti come se fossero veri.

### Caricamento

Lo strumento prepara il pacchetto, lo convalida (`preflight`) e invia solo se lo chiedi esplicitamente: `push` parte in dry-run, l'invio vero richiede `--live` e le tue credenziali via variabili d'ambiente, tocca solo gli elementi con `upload.flag = ready` e non sovrascrive mai file esistenti su Commons.

#### Cosa fornisce il pacchetto

Per ogni elemento, `pubdom.json` contiene tutto ciò che serve: il blocco `{{Information}}` (descrizione, data, fonte, autore), i template di licenza già convalidati sui cataloghi reali, le categorie proposte e verificate, l'elenco dei file con i loro SHA1 (il controllo duplicati lo fa `preflight`) e le dichiarazioni Wikidata con i QID già risolti. Vanno caricati solo gli elementi con `upload.flag = ready`; con la policy `all` anche gli incerti — ma il motivo resta scritto sull'elemento.

#### L'invio via API (`push`)

```bash
# dry-run (default): nessuna rete, nessuna credenziale — mostra cosa invierebbe
python3 -m pd_checker push Concorsi [--filename-prefix PREFISSO]

# invio vero: servono credenziali; il prefisso è opzionale (flag > contesto > nome cartella)
python3 -m pd_checker push Concorsi --live [--filename-prefix "Prefisso"]
```

**Il prefisso dei nomi file è ora opzionale** — risoluzione in ordine: 1) flag `--filename-prefix`, 2) chiave `upload_prefix` nel contesto (context.json o pubdom.json), 3) nome della cartella (sanificato). Quando un prefisso è derivato automaticamente, il comando stampa la fonte usata.

Ogni file caricato porta automaticamente la categoria nascosta `[[Category:Uploaded with pd-checker]]`, per tracciare gli upload fatti col tool (personalizzabile con `--tracking-category`, disattivabile con `--no-tracking`).

Cosa serve — una delle due vie, mai la password dell'account. Nota: il token OAuth NON si richiede su Commons, l'OAuth di Wikimedia è centralizzato su meta.

**Via A — OAuth 2 owner-only (consigliata, non richiede revisione):**

1. Accedi a meta.wikimedia.org con lo stesso account che usi su Commons e apri `https://meta.wikimedia.org/wiki/Special:OAuthConsumerRegistration/propose/oauth2`.
2. Spunta «This consumer is for use only by `<tuo account>`» — così il consumer è owner-only: niente revisione da parte di nessuno, niente autorizzazione, usabile solo da te.
3. Compila il modulo: un nome a piacere per lo strumento, e tra i permessi ("grants") spunta quelli per modificare pagine e caricare file; il resto può restare vuoto. Invia.
4. Nella pagina di conferma ti viene mostrato direttamente l'**access token** (una stringa lunga che inizia per `ey`): copiala subito, è la cosa più comoda.
5. `export PD_WM_OAUTH_TOKEN='ey…'`

**Via B — bot password (più rapida):**

1. Su `https://commons.wikimedia.org/wiki/Special:BotPasswords` crea una nuova bot password: dai un suffisso al nome, per es. `pd-checker`.
2. Spunta solo i permessi necessari: caricare file (upload) e modificare pagine (edit).
3. Ti viene mostrata la password generata (una volta sola). Il nome utente da usare diventa `<tuo account>@pd-checker`.
4. `export PD_WM_BOT_USER='TuoNome@pd-checker'` e `export PD_WM_BOT_PASSWORD='<la password generata>'`

In entrambi i casi: `export PD_WM_CONTACT='tua@email'` — serve per lo User-Agent richiesto dalla policy Wikimedia. Se cambi la password dell'account, la bot password smette di funzionare finché non la rigeneri.

**Cosa carica**: ogni item con `upload.flag = ready` — con la policy `all` anche gli incerti — ma il motivo resta scritto sull'elemento. Ogni item carica TUTTE le sue immagini (fronte + retro): i retro prendono il suffisso « (retro)» e l'annata (anno di riferimento) è accodata dal tool. Il nome su Commons descrive il contenuto dell'item: è il campo `content_title` scritto dal passo di estrazione (es. «Locandina Ave Maria - Rieti.png») e deve essere unico nel pacchetto; per gli item senza titolo resta lo schema col prefisso e, nei pacchetti multi-item, l'indice « - <n>». Le pagine {{Information}} dei retro sono minime e linkano al fronte.

Output reale del dry-run sul materiale di esempio:

```text
$ python3 -m pd_checker push Cardinale-Del-Val

== Push: Cardinale-Del-Val ==
Items pronti: 8
Modalità: DRY-RUN

[DRY-RUN] Cardinale-Del-Val - 1.png
  File: Cardinale-Del-Val/1_fronte.png
  Wikitext:
    {{Information
     |description=(FRONT, printed): A / LONDRA / 10 ottobre / 1865 ...
     |date=1932
     |source=non specificato (imprint: Stab. Sansaini - Roma)
     |author={{unknown|author}}
     |permission=
    }}
```

Con zero elementi pronti (policy strict) il comando rifiuta ed esce con errore: `[push] 0 pronti su 8; usa --upload-policy per cambiare la politica.`

#### Fatto a mano, se preferisci

Apri commons.wikimedia.org con il tuo account, carica il file dell'elemento da Special:Upload e incolla nel campo descrizione il wikitext assemblandolo dai campi dell'elemento (`{{Information}}` + template di licenza + categorie); le dichiarazioni Wikidata (P31, P170, P180, P571…) si aggiungono nella scheda Structured data, con i QID presi dal pacchetto (per es. P180 = Rafael Merry del Val → `Q507540`).

#### Completare le entità mancanti (`create-entity`)

Quando un'entità (autore, editore, ecc.) finisce senza QID (stato della cache `not_found` o `ambiguous`), il pacchetto genera un suggerimento `entities_to_create` in `pubdom.json`. Il flusso è un gesto solo, dopo una configurazione una volta sola:

1. **Configura la fonte nel contesto della cartella** (`pd_data/context.json`): `"entity_sources": {"Grafiche Baroni - Milano": "https://…"}` e, se vuoi la classe, `"entity_p31": {"…": "Q…"}`. La configurazione è durevole — sopravvive a ogni rigenerazione del pacchetto; senza fonte la creazione viene saltata (`[SALTATO]`).
2. **Controlla il dry-run**: `python3 -m pd_checker create-entity <cartella>` — mostra il payload che verrebbe inviato (etichetta, descrizione, classe, fonte).
3. **Crea tutto in un colpo**: `create-entity <cartella> --live` — crea ogni entità con la fonte pronta, scrive i QID in cache **e rigenera subito il pacchetto**: le dichiarazioni passano a `ok` nella stessa esecuzione, senza passaggi successivi.

### Sviluppo

```bash
python3 -m pytest pd_checker/tests -q
```
