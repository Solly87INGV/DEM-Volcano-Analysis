# MorphoVolc — DEM-Based Volcanic Volume Analysis (Docker Edition)

MorphoVolc è un applicativo web per l’analisi morfometrica di edifici vulcanici a partire da **Digital Elevation Models (DEM)**, con stima di:

- Area della base  
- Larghezza della base  
- Area della caldera  
- Larghezza della caldera  
- Volume totale dell’edificio  
- Volume della caldera  
- Volume effettivo  

L’applicativo è **completamente dockerizzato**, garantendo:

- Riproducibilità  
- Portabilità  
- Ambiente isolato  
- Nessuna installazione manuale di Python o Node.js  

---

## Requisiti

È necessario installare **solo**:

- **Docker Desktop**

Non sono richiesti:

- Python  
- Node.js  
- Git  
- Librerie scientifiche  

---

## Installazione

### 1) Installare Docker Desktop

Scaricare Docker Desktop dal sito ufficiale:

https://www.docker.com/products/docker-desktop/

Installare con impostazioni di default e verificare che Docker sia in esecuzione.

### 2) Scaricare il progetto

Scaricare il progetto come file **ZIP** da GitHub e decomprimerlo in una cartella locale.

Esempio:

`C:\Users\NomeUtente\Desktop\MorphoVolc`

---

## Avvio dell’applicativo

Aprire un terminale nella cartella principale del progetto (dove è presente `docker-compose.yml`) ed eseguire:

```bash
docker compose up --build
