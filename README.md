# Public Domain Checker for Wiki Commons

Un'Agent Skill che decide se materiale scannerizzato (fotografie, cartoline, lettere, santini, ecc) è di pubblico dominio, e prepara i metadati necessari per pubblicarlo su Wikimedia Commons e Wikidata.

Legge una cartella di materiale e un'estrazione leggibile dalla macchina di cosa sia e cosa dica ciascun elemento. Applica in modo deterministico le regole di copyright per Italia, Regno Unito e Vaticano, e scrive un unico file JSON portabile per cartella più una dashboard HTML autonoma per la revisione umana.

Ogni decisione è spiegabile: lo strumento indica la base legale, la versione delle tabelle regole e l'affidabilità. Non inventa mai un identificativo, un template di licenza o una categoria: ciascuno è convalidato su un catalogo Wikimedia reale prima di essere scritto.

Gli internals del pacchetto sono descritti in [`pd_checker/README.md`](pd_checker/README.md). La documentazione per agenti, [`pd_checker/SKILL.md`](pd_checker/SKILL.md), è volontariamente in inglese.

Esempio di dashboard generata da questo progetto: [`dashboard.html`](dashboard.html) (senza le immagini).

## Cosa non è

- Non è consulenza legale. Il verdetto è deterministico e documentato; la decisione finale di pubblicare è tua.
- Non è autonomo. Una persona rivede la dashboard (file generato) ed esegue ogni caricamento.

## Percorso normale: con l'agente

1. **Prepara la cartella di scansioni.** Metti le scansioni in una cartella del progetto, per esempio `Concorsi/`. Non prepari nessun altro file: la cartella di scansioni è l'unico input.

2. **Chiedi all'agente.** Apri l'agente nella cartella del progetto e chiedi: «Analizza la cartella Concorsi seguendo pd_checker/SKILL.md». Il file [`pd_checker/SKILL.md`](pd_checker/SKILL.md) contiene le istruzioni complete per l'agente, in inglese; indicandolo nel prompt il percorso funziona con qualsiasi agente capace, anche senza auto-scoperta della skill.

3. **L'agente legge le scansioni, il motore decide.** L'agente legge ogni scansione con il suo modello a visione e scrive l'estrazione dei fatti in `Concorsi/pd_data/facts.json`: tipo di documento, date, nomi, testi, classificazione. Poi esegue il motore `pd_checker`, che applica in modo deterministico le regole di copyright. Il modello estrae i fatti; il motore decide la legge. Tu non prepari nulla oltre la cartella: `facts.json` lo produce il passo di estrazione dell'agente.

4. **Rivedi la dashboard.** L'agente produce `Concorsi/pubdom.json` e `Concorsi/dashboard.html`. Apri `Concorsi/dashboard.html` nel browser: per ogni elemento trovi il verdetto, l'affidabilità, la base legale, l'evidenza della data, i metadati Wikimedia proposti e le miniature. Se l'agente lavora in un ambiente remoto, scarica prima i file dalla cartella del progetto.

5. **Carica quando gli elementi sono *Pronto*.** Il caricamento parte sempre in dry-run: `push` mostra cosa invierebbe, senza rete e senza credenziali. Prima dell'invio, `preflight` verifica il pacchetto (duplicati su Commons, template e categorie) e segnala ogni blocco con il motivo. L'invio vero richiede `--live` e le tue credenziali Wikimedia, un token OAuth owner-only o una bot password: la procedura completa per crearle è nel blocco sviluppatori qui sotto. I file già esistenti su Commons non vengono mai sovrascritti.

## Come si decide il verdetto

Il motore valuta il paese d'origine e l'Italia applicando la regola del termine più breve: pubblico dominio solo se lo è in entrambi. I template di licenza descrivono sempre il paese d'origine, quindi un'opera non italiana non riceve mai un template italiano.

Ogni verdetto riporta l'affidabilità, la base legale e il motivo. Dati mancanti e vincoli non-copyright sono indicati separatamente. Quando il verdetto è `incerto` non viene emesso alcun template di licenza.

Il dettaglio legale per ciascun paese è in [`publicdomain-checker.md`](publicdomain-checker.md).

## Limiti

Il verdetto è tanto buono quanto l'estrazione: l'agente legge le scansioni con un modello a visione e un errore di lettura (una data, un nome) si propaga al verdetto, per questo la dashboard mostra l'evidenza della data da verificare. Un nome assente dalla cache non riceve QID e resta `da_rivedere`; alcune questioni sui termini UK e USA restano aperte, segnalate in [`publicdomain-checker.md`](publicdomain-checker.md); le dichiarazioni Wikidata strutturate restano un passaggio manuale.

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
```

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
- `dashboard.html` — una pagina statica (interfaccia in italiano, nessun JavaScript) con il riepilogo dei verdetti, la scheda di ogni elemento, l'evidenza della data, i metadati Wikimedia proposti e le miniature cliccabili.
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
python3 -m pd_checker push Concorsi

# invio vero: servono credenziali e un prefisso per i nomi dei file
python3 -m pd_checker push Concorsi --live --filename-prefix "Concorsi"
```

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

**Cosa carica**: ogni item con `upload.flag = ready` — con la policy `all` anche gli incerti — ma il motivo resta scritto sull'elemento. Ogni item carica TUTTE le sue immagini (fronte + retro), con i retro nominati `(<prefix> - <n> (retro).png)` e pagine {{Information}} minime che linkano al fronte.

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

### Sviluppo

```bash
python3 -m pytest pd_checker/tests -q
```

Conteggio attuale: 242 test.
