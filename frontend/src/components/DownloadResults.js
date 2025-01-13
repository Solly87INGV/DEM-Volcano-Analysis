import React from 'react';
import { Button } from '@mui/material';
import { jsPDF } from 'jspdf';
import DownloadIcon from '@mui/icons-material/Download';
import './DownloadResults.css'; // Assicurati che il CSS sia importato

const DownloadResults = ({ result }) => {
  const handleDownload = () => {
    const doc = new jsPDF('p', 'mm', 'a4');

    // Aggiunge l'immagine di sfondo
    const background = '/images/ingv_Etna.jpg';
    const logo = '/images/logo-ingv.jpeg';
    doc.addImage(background, 'JPEG', 0, 0, 210, 50); // Immagine di sfondo che copre l'header
    doc.addImage(logo, 'JPEG', 10, 10, 30, 30); // Logo in alto a sinistra

    // Aggiunge il titolo
    doc.setFontSize(16);
    doc.setTextColor(255, 255, 255); // Testo bianco per visibilità
    doc.text('Interface for DEM processing', 43, 40);

    // Aggiunge una linea separatrice
    doc.setDrawColor(0, 0, 0);
    doc.setLineWidth(0.5);
    doc.line(10, 55, 200, 55);

    // Definizione delle variabili per la tabella
    const lines = result.split('\n'); // Assume che "result" sia una stringa multilinea dinamica
    doc.setFontSize(12);
    doc.setTextColor(0, 0, 0);
    let y = 65; // Posizione iniziale verticale per i risultati

    const cellPadding = 5; // Padding interno delle celle
    const col1X = 10; // Inizio della prima colonna
    const col1Width = 140; // Larghezza della colonna descrizioni
    const col2X = col1X + col1Width; // Inizio della seconda colonna
    const col2Width = 50; // Larghezza della colonna valori
    const rowHeight = 10; // Altezza di ogni riga

    lines.forEach((line, index) => {
      // Rimuove caratteri non visibili e spazi extra
      const cleanLine = line.replace(/[\r\n:]+/g, '').trim(); // Elimina anche i due punti

      // Regex aggiornata per separare etichetta, numero e unità
      const match = cleanLine.match(/^(.*?)\s+([\d.]+)\s*(km²|km³|km)?$/); // Regex senza `:`
      if (match) {
        const [, label, number, unit] = match;

        const value = `${number} ${unit || ''}`.trim();

        // Disegna la cella della descrizione
        doc.rect(col1X, y, col1Width, rowHeight); // Rettangolo per la descrizione
        doc.setFont('helvetica', 'normal');
        doc.text(label, col1X + cellPadding, y + rowHeight / 2 + 3); // Testo centrato verticalmente

        // Disegna la cella del valore
        doc.rect(col2X, y, col2Width, rowHeight); // Rettangolo per il valore
        doc.text(value, col2X + cellPadding, y + rowHeight / 2 + 3); // Testo centrato verticalmente
      } else {
        console.warn(`Line ${index + 1} did not match regex: "${cleanLine}"`);
      }

      y += rowHeight; // Incrementa la posizione verticale
    });

    // Salva il PDF
    doc.save('CalculationResults.pdf');
  };

  return (
    <Button
      onClick={handleDownload}
      className="download-button" // Usa il CSS esistente
      startIcon={<DownloadIcon />}
    >
      Download Full results
    </Button>
  );
};

export default DownloadResults;
