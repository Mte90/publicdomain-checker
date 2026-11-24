# Come il sistema decide se un'opera è di pubblico dominio

Questo documento spiega in dettaglio il **motore di valutazione automatica**
del PD Archive Checker: come analizza un'immagine, quali regole applica, e
come arriva al verdetto `public_domain` / `not_public_domain` / `uncertain`.

---

## Pipeline completa (per ogni immagine)

```
┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│  1. OCR      │──>│  2. VLM      │──>│  3. Wikidata  │──>│  4. Regole    │
│  (opzionale) │   │  analisi     │   │  lookup      │   │  L. 633/1941  │
│  deepseek-ocr│   │  gpt-4o      │   │  autore/     │   │  motore       │
│  estrae text │   │  + ragiona   │   │  editore     │   │  valutazione  │
└──────────────┘   └──────────────┘   └──────────────┘   └──────────────┘
                                                              │
                                                              v
                                                       ┌──────────────┐
                                                       │  5. Output   │
                                                       │  verdetto +  │
                                                       │  template    │
                                                       │  Commons     │
                                                       └──────────────┘
```

---

## Step 1: OCR pre-extraction (opzionale)

Se il provider OCR è configurato (`OCR_*` env vars o `--ocr-*` flags), il
sistema estrae prima il testo grezzo dall'immagine usando un modello
specializzato (es. `deepseek-ocr`).

**Perché questo step?** Modelli OCR specializzati sono molto più accurati
del VLM generico per testo denso (giornali, lettere manoscritte). Il testo
estratto viene passato al VLM come "suggerimento" che può verificare contro
l'immagine.

**Se non configurato**: il VLM fa da solo entrambi i compiti.

---

## Step 2: Analisi VLM

Il VLM (es. `gpt-4o`, `deepseek-vl`) riceve:
- L'immagine
- (Opzionale) il testo OCR pre-estratto come riferimento
- Un prompt strutturato che chiede di restituire un JSON con:
  - `document_type`: `photo` | `postcard` | `letter` | `newspaper` | `magazine` | `manuscript` | `unknown`
  - `visible_text`: dict con `date_seen`, `author_or_photographer`, `newspaper_masthead`, `signature`, `recipient`, `sender`, ecc.
  - `visual_era_estimate`: decennio stimato + indizi visivi
  - `is_anonymous`: true se non c'è nome/firma/credito
  - `photograph_classification`: `simple` | `artistic` | `unclear`
  - `confidence.overall`: `high` | `medium` | `low`
  - `raw_ocr`: testo trascritto fedelmente

Il VLM fornisce sia **dati oggettivi** (testo, data) sia **giudizi
interpretativi** (semplice vs artistica, epoca stimata) con livello di
confidenza.

---

## Step 3: Lookup Wikidata

Per ogni nome identificato nel testo visibile (autore, editore, testata), il
sistema interroga Wikidata via SPARQL:

- **Persone** (fotografi, giornalisti, autori): recupera `P570` (data di
  morte) e `P569` (data di nascita). Questo è fondamentale per calcolare la
  scadenza "vita + 70 anni".
- **Editori/Testate** (giornali, riviste): recupera `P571` (inception) e
  `P576` (dissolved), per stimare il periodo di attività.

I risultati sono cachati su SQLite per evitare richieste duplicate sullo
stesso nome nel corso di un batch >500 file.

**Se l'autore non è in Wikidata**: il sistema non può calcolare la
scadenza vita+70 e torna `uncertain` chiedendo all'utente di verificare
manualmente.

---

## Step 4: Motore regole L. 633/1941

Il cuore del sistema è il modulo `rules.py`. Applica le regole
della **Legge italiana 22 aprile 1941 n. 633** (diritto d'autore) e le
direttive UE sull'armonizzazione dei termini.

### Anno di riferimento

Per calcolare se un'opera è PD, il sistema cerca l'anno più rilevante in
quest'ordine di priorità:
1. Data leggibile nell'immagine (`visible_text.date_seen`)
2. Anno fornito dall'utente come hint (metadati EXIF o esterno)
3. Stima del decennio dal VLM (`visual_era_estimate.estimated_decade_start`)

Se nessuno di questi è disponibile, lo status è `uncertain`.

### Anno corrente

Tutti i calcoli usano l'anno corrente (`date.today().year`). I termini della
Sezione III (Artt. 25/26/27/29) decorrono dal 1° gennaio dell'anno successivo
all'evento (Art. 32-ter): un autore morto nel 1955 è in pubblico dominio dal
1° gennaio 2026.

---

## I 5 casi gestiti

### CASO 1: Fotografia

Il sistema distingue due sottocasi:

#### 1a. Fotografia semplice (Art. 92 L. 633/1941)

**Cos'è**: una fotografia "esclusivamente documentaria" senza scelte
creative dell'autore. È protetta da un **diritto connesso** di **70 anni**
dalla produzione dello scatto, non dal diritto d'autore ordinario.

**Termine vigente**: **70 anni dalla produzione** (era 20 fino al 17 dicembre
2025); la durata è stata portata a 70 anni dall'**art. 47 della Legge
2 dicembre 2025 n. 182**, in vigore dal 18 dicembre 2025.

**Condizione PD**: sono passati 70 anni dallo scatto.
```
pd_year = reference_year + 70
if pd_year <= current_year: PUBLIC DOMAIN
```

**Caveat transitorio**: la Legge 182/2025 non contiene norme transitorie, quindi
non è pacifico se le foto prodotte prima del 18 dicembre 2025 — già in pubblico
dominio per i vecchi 20 anni — siano state "ri-protette" dai 70 anni. Il sistema
marca come `uncertain` ogni foto semplice il cui scatto cade nell'intervallo
[anno_corrente − 69, anno_corrente − 20]: erano PD con la vecchia regola, non lo
sono con la nuova.

**Regola transitoria applicata**: le foto il cui termine ventennale era già
scaduto **prima del 18 dicembre 2025** restano in pubblico dominio, perché un
termine una volta scaduto non si riattiva. In pratica:

| Scatto | Esito | Confidenza |
| --- | --- | --- |
| **≤ 2004** | pubblico dominio (nessun revival) | `media` |
| **≥ 2005** | protetta **70 anni dalla produzione** | `alta` |

**Template Commons**: `{{PD-Italy}}` + `{{PD-1996}}` per le foto semplici italiane.
**Non** usare `{{PD-Italy-photo}}` (non esiste, 404) né `{{PD-old-70}}` su una foto anonima.

**Foto artistica anonima**: `{{PD-Italy}}` **non** è corretto (codifica il termine
ventennale delle foto semplici). Usare `{{PD-anon-70-EU}}` + `{{PD-1996}}`.

**Tag lato Stati Uniti**: al tag del paese d'origine va sempre affiancato un tag
US (`{{PD-1996}}`) quando l'opera era in pubblico dominio nel paese d'origine al
1° gennaio 1996 (data URAA).

#### 1b. Fotografia artistica / d'autore (Art. 2 + Art. 25)

**Cos'è**: una fotografia con "carattere creativo" (composizione,
illuminazione, scelta del soggetto rivelano originalità). Protetta come
opera dell'ingegno, **vita autore + 70 anni**.

**Condizione PD (autore noto)**:
```
pd_year = author_death_year + 70
if pd_year <= current_year: PUBLIC DOMAIN
```

**Condizione PD (anonima)**: 70 anni dalla pubblicazione (Art. 27).
```
pd_year = reference_year + 70
if pd_year <= current_year: PUBLIC DOMAIN
```

**Template Commons**: `{{PD-old-70}}` + `{{PD-1996}}` (autore morto >70y fa)

#### Caso "unclear" (foto molto vecchia)

Se il VLM non sa dire se la foto è semplice o artistica, ma l'immagine è
vecchia di oltre 100 anni (es. datata 1910), il sistema restituisce
`uncertain` con bassa confidenza: entrambe le strade porterebbero a PD,
ma l'utente deve confermare la classificazione.

---

### CASO 2: Cartolina illustrata

Trattata come foto:
- **Cartolina "artistica"** (con autore noto): vita + 70 (Art. 2 + Art. 25)
- **Cartolina "semplice"** (stampata in serie, foto standard): 70 anni dal
  diritto connesso fotografico (Art. 92)

Le cartoline illustrate con foto stampate in serie rientrano quasi sempre
nella seconda categoria.

---

### CASO 3: Lettera privata (CASO SPINOSO)

**Regola**: il diritto d'autore resta dell'autore (vita + 70 anni, Art. 25).
In più l'Art. 93 pone un **diritto di consenso a durata indeterminata**: le
corrispondenze confidenziali non possono essere pubblicate senza il consenso
dell'autore e, per le lettere, anche del destinatario; dopo la loro morte
senza il consenso — nell'ordine — del coniuge e dei figli, poi dei genitori,
poi dei fratelli e delle sorelle, e infine degli ascendenti e discendenti
diretti fino al quarto grado. In caso di dissenso decide l'autorità
giudiziaria; è rispettata la volontà scritta del defunto.

**Non esiste alcun termine di 20 anni**: il diritto dell'Art. 93 non si estingue
con il copyright e non ha scadenza fissa.

**Il sistema le segna SEMPRE come `uncertain`**, anche quando sarebbero PD
per scadenza copyright. Le checklist chiedono sempre:

- Identificare mittente (autore) e destinatario
- Verificare implicazioni di privacy
- Verificare se gli eredi del destinatario hanno obiezioni
- Considerare se contattare gli eredi per autorizzazione esplicita

**Questo è il caso dove il tool è più conservativo.**

---

### CASO 4: Giornale / Rivista

Distinzione tra:

#### Singolo articolo firmato

Se l'articolo è firmato da un autore identificato:
- Diritto d'autore dell'autore: **vita + 70 anni** (Art. 2 + Art. 25)
- Il fatto notiziabile non è coperto, ma l'espressione creativa sì.

#### Numero del periodico (opera collettiva)

L'intero numero è un'opera collettiva periodica. Il diritto di utilizzazione
economica spetta all'editore (Art. 38), ma la durata è quella dell'opera
collettiva: **70 anni dalla prima pubblicazione** (Artt. 26 e 30), calcolata
dalla fine dell'anno (Art. 30). **Non esiste** un separato "diritto connesso
dell'editore" di 20 anni: l'Art. 79 riguarda le emittenti radiotelevisive
(50 anni), non gli editori.

**Condizione PD**: 70 anni dalla pubblicazione.
```
pd_year = reference_year + 70
if pd_year <= current_year: PUBLIC_DOMAIN (con caveat)
```

**Template Commons**: `{{PD-anon-70-EU}}` + `{{PD-1996}}`

**ATTENZIONE**: anche se il numero è PD come opera collettiva anonima, i
singoli articoli firmati da autori morti da meno di 70 anni restano
coperti. Il sistema lo segnala nel verdetto.

---

### CASO 5: Manoscritto

Come per le opere testuali:
- **Autore noto**: vita + 70 anni (Art. 2 + Art. 25)
- **Anonimo**: 70 anni dalla pubblicazione (Art. 27)

---

## Output del motore regole

Per ogni file il sistema produce un `CopyrightVerdict` con:

```python
{
    "status": "public_domain" | "not_public_domain" | "uncertain",
    "confidence": "high" | "medium" | "low",
    "reason": "spiegazione human-readable del calcolo",
    "legal_basis": ["Art. 92, comma 1 - L. 633/1941", ...],
    "commons_templates": ["{{PD-Italy}}", "{{PD-1996}}", ...],  # foto semplici
    "commons_categories": ["Public domain photographs of Italy", ...],
    "missing_data": ["Verificare data di morte di 'Mario Rossi'", ...],
    "pd_year": 1980,            # anno in cui è diventato PD
    "years_until_pd": null      # se non PD, anni mancanti
}
```

---

## Tabella riassuntiva dei termini

| Tipo opera | Articolo | Termine | Decorrenza |
|-----------|----------|---------|-----------|
| Foto semplice | Art. 92 (L. 182/2025) | 70 anni | dalla produzione dello scatto |
| Foto artistica (autore noto) | Art. 2 + 25 | 70 anni | dalla morte dell'autore |
| Opera anonima | Art. 27 | 70 anni | dalla prima pubblicazione |
| Opera pseudonima | Art. 27 | 70 anni | dalla prima pubblicazione |
| Opera collettiva | Art. 26 | 70 anni | dalla prima pubblicazione |
| Giornale / rivista (periodico) | Artt. 26 + 30 | 70 anni | dalla prima pubblicazione (fine anno) |
| Lettera privata | Art. 25 + 93 | copyright vita+70; Art. 93 consenso a durata indeterminata | — |
| Opere in generale | Art. 25 | 70 anni | dalla morte dell'autore (regola generale) |
| Diritti di enti pubblici/accademie | Art. 29 | 20 anni | dalla prima pubblicazione |
| Trasmissioni radiotelevisive | Art. 79 | 50 anni | dalla prima diffusione |

**NOTA BENE**: questi sono i termini italiani. La "rule of the shorter term"
(Art. 188 L. 633/1941) si applica solo alle opere il cui Paese d'origine è
extra-UE: per le opere UE/SEE vale il termine italiano pieno, senza confronto
(Direttiva 2006/116/CE, art. 7).

---

## Confidenza: come viene calcolata

Il sistema combina due livelli di confidenza:

1. **Confidenza del motore regole** (`high` se i dati sono completi e
   chiari, `medium` se ci sono giudizi interpretativi coinvolti, `low`
   se mancano dati essenziali)
2. **Confidenza del VLM** (auto-riferita dal modello: `high`/`medium`/`low`)

**La peggiore delle due vince**: se il motore dice `high` ma il VLM ha
letto la data con `low` confidence, il verdetto finale è `low`.

### Quando il sistema è `uncertain`

Il sistema ritorna `uncertain` (con checklist di cosa manca) quando:

- **Tipo documento non riconosciuto** dal VLM
- **Data mancante**: impossibile calcolare scadenze temporali
- **Autore identificato ma non in Wikidata**: impossibile calcolare
  vita + 70
- **Foto molto vecchia ma "unclear" semplice vs artistica**
- **Lettera privata** (sempre uncertain, vedi sopra)

---

## Cosa NON è coperto

Il sistema **non valuta**:

1. **Diritti della personalità** (Art. 96-97 L. 633/1941): foto con persone
   riconoscibili possono richiedere consenso della persona raffigurata o
   degli eredi, anche se l'opera è PD per copyright.

2. **Diritti di corrispondenza** per lettere private (segnalato come
   `uncertain` ma non valutato automaticamente).

3. **Diritti connessi di interpreti/produttori** per registrazioni
   audio/video (non applicabile a materiale cartaceo d'archivio).

4. **Marchi, design, loghi** eventualmente presenti nell'immagine.

5. **Privacy dei discendenti** per lettere/cartoline personali.

**Per questi aspetti serve sempre revisione manuale**, anche quando il
sistema dice `public_domain` con `high` confidence.

---

## Esempio di ragionamento

### Input: foto datata 1910, anonima, VLM la classifica "simple"

```
VLM result:
  document_type: photo
  visible_text.date_seen: 1910
  is_anonymous: true
  photograph_classification.type: simple
  confidence.overall: high

Motore regole:
  photo_type = "simple"
  reference_year = 1910
  pd_year = 1910 + 70 = 1980
  current_year = 2026

  1980 <= 2026 -> PUBLIC DOMAIN

  confidence finale = min("high" motor, "high" VLM) = "high"

Verdetto:
  status: public_domain
  confidence: high
  reason: "Fotografia semplice scattata nel/before 1910.
           Il diritto connesso (Art. 92, 70 anni) è scaduto nel 1980.
           Attualmente in pubblico dominio in Italia."
  legal_basis: ["Art. 92, comma 1 - L. 633/1941"]
  commons_templates: ["{{PD-Italy}}", "{{PD-1996}}"]  # foto semplice anonima
  commons_categories: ["Public domain photographs of Italy",
                       "Photographs of Italy in the 1900s"]
  pd_year: 1980
```

### Input: lettera di Italo Svevo (1925, morto 1928)

```
VLM result:
  document_type: letter
  visible_text.date_seen: 1925
  visible_text.author_or_photographer: "Italo Svevo"
  is_anonymous: false
  confidence.overall: high

Wikidata lookup "Italo Svevo":
  found: true
  death_date: 1928-09-13

Motore regole:
  author_death_year = 1928
  pd_year_copyright = 1928 + 70 + 1 = 1999  (copyright scaduto; Art. 25 + 32-ter)
  current_year = 2026

  Copyright scaduto nel 1999, quindi PD per copyright.
  MA: lettera privata -> sempre uncertain: l'Art. 93 non ha scadenza fissa.

Verdetto:
  status: uncertain   <-- SEMPRE per lettere
  confidence: medium
  reason: "Lettera di Italo Svevo (morto nel 1928).
           Diritto d'autore (vita+70) scaduto nel 1999.
           MA resta il diritto di consenso dell'Art. 93, che non ha
           scadenza fissa: serve il consenso del destinatario e, dopo
           la sua morte, dei suoi eredi. Rimane inoltre la questione
           della privacy dei contenuti."
  legal_basis: ["Art. 25", "Art. 93 - L. 633/1941"]
  commons_templates: ["{{PD-old-70}}", "{{PD-Italy}}"]
  missing_data: [
    "Identificare il destinatario della lettera di Italo Svevo",
    "Verificare se il contenuto ha implicazioni private/familiari",
    "Considerare se contattare gli eredi dell'autore per autorizzazione"
  ]
  pd_year: 1999
```

---

## Giurisdizioni estere (Regno Unito, Vaticano)

Il motore valuta il **paese d'origine** *e* l'Italia e applica la **regola del
termine più breve**: l'opera è in pubblico dominio solo se lo è in entrambi.
I tag Commons però descrivono **solo il paese d'origine** — un'opera non italiana
non riceve mai un template italiano.

### Regno Unito (CDPA 1988)

| Caso | Regola | Riferimento |
| --- | --- | --- |
| Autore noto | vita + 70 anni | CDPA s.12(2) |
| **Autore ignoto** | **70 anni dalla fine dell'anno di creazione** | **CDPA s.12(3)** |
| Fotografia pre-1957 | termine di 50 anni dalla realizzazione (1911 Act s.21), scaduto il 31/12/1960 | CDPA Sch.1 para 12(2)(c) |

Quindi una fotografia inglese del **1910 con autore ignoto è in pubblico dominio**:
non serve la data di morte del fotografo.

**Template Commons**: `{{PD-anon-70}}` + `{{PD-1996}}` (autore ignoto);
`{{PD-old-70}}` + `{{PD-1996}}` (autore noto).

### Vaticano

Termine: **70 anni** (L. CXCVII/2017).

**Non esiste alcun template di paese su Commons per il Vaticano** (verificato sul
catalogo reale: 0 corrispondenze). Si usano i generici `{{PD-anon-70-EU}}`
(anonimo) oppure `{{PD-old-70}}` (autore noto), più `{{PD-1996}}`.

---

## Riferimenti normativi

- **L. 22 aprile 1941 n. 633** — Protezione delle opere dell'ingegno e dei
  diritti connessi (testo consolidato su Normattiva)
- **Legge 2 dicembre 2025 n. 182, art. 47** — estende da 20 a 70 anni la durata
  del diritto esclusivo sulle fotografie semplici (Art. 92); in vigore dal
  18 dicembre 2025
- **Direttiva 2006/116/CE** — Armonizzazione dei termini di protezione del
  diritto d'autore (70 anni post mortem auctoris)
- **Direttiva 93/98/CEE** — Direttiva originaria sull'armonizzazione
- **Convenzione di Berna** — Protezione delle opere letterarie e artistiche
- [Manuale di diritto d'autore su Wikimedia](https://it.wikipedia.org/wiki/Wikipedia:Diritti_d%27autore)

---

## Disclaimer finale

Questo documento e il motore di valutazione forniscono una **valutazione
automatica** basata su regole della L. 633/1941. **NON è una consulenza
legale**. La decisione finale di caricare un file su Wikimedia Commons resta
**responsabilità dell'utente**.

Le casistiche più rischiose (lettere private, foto con persone riconoscibili,
opere non chiaramente classificabili) vengono **sempre** segnalate come
`uncertain` dal sistema per forzare la revisione umana.
