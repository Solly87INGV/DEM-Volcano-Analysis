# pdf_generator.py

import os
from reportlab.lib.pagesizes import letter
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, PageBreak, Flowable
)
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY  # Importa l'enum per la giustificazione

class ImageWithText(Flowable):
    """
    Custom Flowable to draw an image with overlaid text.
    """
    def __init__(self, image_path, text, font_size=10, text_color=colors.white, x=10, y=10, width=400, height=100):
        Flowable.__init__(self)
        self.image_path = image_path
        self.text = text
        self.font_size = font_size
        self.text_color = text_color
        self.x = x
        self.y = y
        self.width = width
        self.height = height

    def draw(self):
        # Draw the image
        if os.path.exists(self.image_path):
            self.canv.drawImage(self.image_path, 0, 0, width=self.width, height=self.height)
        else:
            # If image does not exist, fill with a rectangle
            self.canv.setFillColor(colors.lightgrey)
            self.canv.rect(0, 0, self.width, self.height, fill=1)
        
        # Set the font and color for the text
        self.canv.setFont("Helvetica-Bold", self.font_size)
        self.canv.setFillColor(self.text_color)
        
        # Draw the text
        self.canv.drawString(self.x, self.y, self.text)

def generate_pdf(file_path, results_list, title="Calculation Results"):
    """
    Genera un PDF con un'immagine di intestazione, un logo, una tabella di risultati e descrizioni dettagliate.

    Parametri:
    - file_path (str): Percorso dove verrà salvato il PDF.
    - results_list (list): Lista di stringhe contenenti "Descrizione: Valore".
    - title (str): Titolo del documento PDF, include informazioni sul processo.
    """
    try:
        # Definisci le dimensioni del carattere come variabili
        TITLE_FONT_SIZE = 14  # Ridotto per adattarsi in una singola riga
        DESCRIPTION_FONT_SIZE = 10
        METHOD_TITLE_FONT_SIZE = 10
        METHOD_BODY_FONT_SIZE = 9
        HEADER_TITLE_FONT_SIZE = 10  # Dimensione del font per il titolo sull'immagine

        # Crea un documento usando SimpleDocTemplate
        doc = SimpleDocTemplate(file_path, pagesize=letter)
        elements = []

        # Ottieni gli stili predefiniti
        styles = getSampleStyleSheet()
        # Definisci stili personalizzati con dimensioni del carattere ridotte e giustificazione
        styles.add(ParagraphStyle(
            name='DescriptionTitle',
            fontSize=12,
            leading=14,
            spaceAfter=6,
            fontName='Helvetica-Bold',
            alignment=TA_JUSTIFY
        ))
        styles.add(ParagraphStyle(
            name='DescriptionBody',
            fontSize=DESCRIPTION_FONT_SIZE,
            leading=12,
            spaceAfter=12,
            alignment=TA_JUSTIFY
        ))
        styles.add(ParagraphStyle(
            name='MethodTitle',
            fontSize=METHOD_TITLE_FONT_SIZE,
            leading=12,
            spaceAfter=6,
            fontName='Helvetica-Bold',
            alignment=TA_JUSTIFY
        ))
        styles.add(ParagraphStyle(
            name='MethodBody',
            fontSize=METHOD_BODY_FONT_SIZE,
            leading=11,
            leftIndent=20,
            spaceAfter=12,
            alignment=TA_JUSTIFY
        ))
        styles.add(ParagraphStyle(
            name='HeaderTitle',
            fontSize=HEADER_TITLE_FONT_SIZE,
            leading=12,
            spaceAfter=4,
            alignment=TA_JUSTIFY,
            fontName='Helvetica-Bold'
        ))
        # Definisci un nuovo stile per il titolo ridotto
        styles.add(ParagraphStyle(
            name='TitleSmall',
            parent=styles['Title'],
            fontSize=TITLE_FONT_SIZE,  # Ridotto per adattarsi in una singola riga
            alignment=TA_JUSTIFY
        ))

        # Percorsi alle immagini
        script_dir = os.path.dirname(os.path.abspath(__file__))
        header_image_path = os.path.join(script_dir, '..', '..', 'frontend', 'public', 'images', 'ingv_Etna.jpg')
        logo_image_path = os.path.join(script_dir, '..', '..', 'frontend', 'public', 'images', 'logo-ingv.jpeg')

        header_image_path = os.path.normpath(header_image_path)  # Normalizza il percorso per compatibilità cross-platform
        logo_image_path = os.path.normpath(logo_image_path)

        # Verifica che entrambe le immagini esistano
        if not os.path.exists(header_image_path):
            raise FileNotFoundError(f"Immagine di intestazione non trovata nel percorso: {header_image_path}")
        if not os.path.exists(logo_image_path):
            raise FileNotFoundError(f"Logo non trovato nel percorso: {logo_image_path}")

        # Crea oggetti Image per il logo
        logo = Image(logo_image_path)

        # Imposta le dimensioni delle immagini
        logo_width = 80  # Larghezza desiderata per il logo
        logo_height = 80  # Altezza desiderata per il logo
        logo.drawWidth = logo_width
        logo.drawHeight = logo_height

        # Crea il paragrafo per il titolo sull'immagine dell'Etna
        header_title_text = "Interface for DEM processing"

        # Crea un Flowable con l'immagine dell'Etna e il testo sovrapposto
        etna_with_text = ImageWithText(
            image_path=header_image_path,
            text=header_title_text,
            font_size=HEADER_TITLE_FONT_SIZE,
            text_color=colors.white,
            x=10,  # Posizione orizzontale del testo
            y=10,  # Posizione verticale del testo
            width=400,  # Larghezza dell'immagine
            height=100  # Altezza dell'immagine
        )

        # Crea una tabella con due colonne: sinistra (logo) e destra (Etna con testo)
        header_table = Table([
            [logo, etna_with_text]
        ], colWidths=[logo_width + 20, 400 + 20])  # Aggiungi padding tra le immagini

        # Definisci lo stile della tabella principale
        header_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),  # Allinea verticalmente al top
            ('LEFTPADDING', (0, 0), (-1, -1), 10),
            ('RIGHTPADDING', (0, 0), (-1, -1), 10),
            ('TOPPADDING', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ]))

        # Aggiungi la tabella all'elemento del documento
        elements.append(header_table)
        elements.append(Spacer(1, 12))  # Aggiungi spazio dopo l'intestazione

        # Aggiungi il titolo al PDF con lo stile 'TitleSmall'
        title_paragraph = Paragraph(title, styles['TitleSmall'])
        elements.append(title_paragraph)
        elements.append(Spacer(1, 12))  # Aggiungi spazio verticale dopo il titolo

        # Prepara i dati per la tabella dei risultati
        table_data = [["Description", "Value"]]

        for line in results_list:
            if ':' in line:
                description, value = line.split(':', 1)
                table_data.append([description.strip(), value.strip()])
            else:
                # Se la riga non contiene ':', aggiungi solo la descrizione
                table_data.append([line.strip(), ""])

        # Crea la tabella con larghezze delle colonne adeguate
        table = Table(table_data, colWidths=[300, 200])  # Regola le larghezze secondo necessità

        # Definisci lo stile della tabella
        style = TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),  # Sfondo della prima riga
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),  # Colore del testo della prima riga

            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),  # Allinea il testo al centro
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),  # Font della prima riga
            ('FONTSIZE', (0, 0), (-1, 0), 12),  # Dimensione del font della prima riga
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),  # Padding inferiore della prima riga

            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),  # Sfondo delle righe rimanenti
            ('GRID', (0, 0), (-1, -1), 1, colors.black),  # Bordi della tabella
        ])
        table.setStyle(style)

        # Aggiungi la tabella agli elementi del documento
        elements.append(table)
        elements.append(Spacer(1, 36))  # Aggiungi più spazio dopo la tabella

        # Definisci le descrizioni dettagliate
        descriptions = [
            {
                'title': '1. Base Area of the Volcano',
                'description': 'The base area of the volcano represents the total surface area bounded by the contour of the volcano\'s base. It is an essential measure for understanding the size and extent of the volcanic structure.',
                'method': [
                    'Identification of the Base Contour: The base contour of the volcano is defined by identifying areas at a specific elevation, calculated as a fraction (e.g., 5%) between the minimum and maximum elevations of the Digital Elevation Model (DEM).',
                    'Area Calculation: Using Gauss\'s formula, the area of the polygon defined by the base contour is calculated without approximations. The obtained value in pixels is then converted to square kilometers (km²) considering the pixel size in the DEM.'
                ]
            },
            {
                'title': '2. Base Width (Distance between Opposite Points of the Base)',
                'description': 'The base width is the distance measured between two opposite points along the contour of the volcano\'s base. This measure provides an indication of the volcano\'s extended size in a specific direction.',
                'method': [
                    'Identification of Opposite Points: After defining the base contour, two opposite points on the contour are automatically identified. These points are selected considering the contour\'s geometry to ensure an accurate representation of the width.',
                    'Distance Calculation: The Euclidean distance between these two opposite points is calculated and converted from pixels to kilometers using the DEM scale.'
                ]
            },
            {
                'title': '3. Caldera Area of the Volcano',
                'description': 'The caldera area represents the surface bounded by the caldera contour, which is a typical depression present in the structure of many volcanoes.',
                'method': [
                    'Identification of the Caldera Contour: The caldera contour is determined based on slope variation. A specific elevation level (e.g., 80% of the DEM\'s maximum elevation) is identified to trace the caldera\'s contour.',
                    'Area Calculation: Similar to the base area calculation, Gauss\'s formula is applied to determine the area of the polygon defined by the caldera contour, converting the final result to square kilometers (km²).'
                ]
            },
            {
                'title': '4. Caldera Width (Distance between Opposite Points of the Caldera)',
                'description': 'The caldera width is the distance measured between two opposite points along the caldera\'s contour. This measure provides information about the central depression\'s dimensions of the volcano.',
                'method': [
                    'Identification of Opposite Points on the Caldera: Using the slope map derived from the DEM, points with the maximum slopes on the caldera are identified. Subsequently, points opposite to these maximum slope points along the caldera contour are selected.',
                    'Distance Calculation: The Euclidean distance between these two opposite points is calculated and converted from pixels to kilometers based on the DEM scale.'
                ]
            },
            {
                'title': '5. Total Volume of the Volcanic Edifice',
                'description': 'The total volume of the volcanic edifice represents the overall amount of material that constitutes the entire volcano structure, excluding the caldera. It is a crucial measure for assessing the volcano\'s immense mass.',
                'method': [
                    'Approximation Models: Two models have been developed to calculate the volume:',
                    '1. Circular Truncated Cone: It is assumed that the volcano has a base and caldera of approximately circular shape. A truncated cone is used to approximate the volcano\'s shape, calculating the base radius (r2) and caldera radius (r1) from the distance between opposite points.',
                    '2. Elliptical Truncated Cone: If the base and caldera have elongated (elliptical) shapes, the volcano is approximated with a truncated cone with elliptical bases, directly using the areas of the bases without calculating the semi-axes.',
                    'Volume Conversion: The obtained volume is converted to cubic kilometers (km³).'
                ]
            },
            {
                'title': '6. Caldera Volume',
                'description': 'The caldera volume represents the space occupied by the volcano\'s central depression. This volume is considered as a "mass-less" portion in the total volcanic edifice.',
                'method': [
                    'Caldera Approximation Models: Two approaches are used to approximate the caldera volume:',
                    '1. Semi-sphere or Cylinder: The caldera can be approximated as a semi-sphere or a cylinder, with the height (depth) set equal to the radius.',
                    '2. Semi-ellipsoid of Rotation or Cylinder with Elliptical Bases: For calderas with more elongated shapes, an ellipsoidal or cylindrical model with elliptical bases is used. In this case, the semi-axes are not calculated, but the areas are directly used.',
                    'Volume Conversion: The calculated volume is converted to cubic kilometers (km³).'
                ]
            },
            {
                'title': '7. Effective Volume of the Volcanic Edifice',
                'description': 'The effective volume represents the actual amount of material that constitutes the volcanic edifice, obtained by subtracting the caldera volume from the total volcanic edifice volume. This value provides a more accurate estimate of the volcano\'s actual mass.',
                'method': [
                    'Where:',
                    '- V_total is the total volume calculated using the truncated cone model.',
                    '- V_caldera is the caldera volume calculated using one of the aforementioned approximation models.'
                ]
            }
        ]

        # Aggiungi le descrizioni dettagliate come sezioni con titoli e paragrafi
        for idx, item in enumerate(descriptions):
            # Titolo della sezione
            title_paragraph = Paragraph(item['title'], styles['DescriptionTitle'])
            elements.append(title_paragraph)

            # Descrizione
            description_paragraph = Paragraph(f"<b>Description:</b> {item['description']}", styles['DescriptionBody'])
            elements.append(description_paragraph)

            # Metodo di Calcolo
            method_title_paragraph = Paragraph("<b>Method of Calculation:</b>", styles['DescriptionBody'])
            elements.append(method_title_paragraph)

            for method in item['method']:
                if isinstance(method, str):
                    # Verifica se il metodo inizia con un numero per formattarlo correttamente
                    if method.strip()[0].isdigit():
                        # Usa un elenco numerato
                        method_paragraph = Paragraph(method, styles['MethodBody'])
                    else:
                        # Usa un elenco puntato manuale
                        method_paragraph = Paragraph(f"- {method}", styles['MethodBody'])
                    elements.append(method_paragraph)
                elif isinstance(method, dict):
                    # Metodo con immagine (non presente nel tuo attuale codice)
                    pass  # Poiché l'immagine non è desiderata, non fare nulla
                # Puoi estendere qui per altri tipi di metodi se necessario

            # Dopo il primo punto, inserisci un PageBreak
            if idx == 0:
                elements.append(PageBreak())

            # Aggiungi uno spazio dopo ogni sezione
            elements.append(Spacer(1, 12))

        # Costruisci il PDF
        doc.build(elements)
    except Exception as e:
        # Per ora, solleva l'eccezione per notificare il chiamante
        raise e
