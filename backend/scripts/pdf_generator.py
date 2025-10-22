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


def generate_pdf(file_path, results_list, title="Calculation Results",
                 image_paths=None, captions=None):
    """
    Genera un PDF con un'immagine di intestazione, un logo, una tabella di risultati,
    descrizioni dettagliate e (opzionale) un blocco di figure alla fine inserite
    una dopo l'altra (senza page break forzati).
    """
    try:
        # Font sizes
        TITLE_FONT_SIZE = 14
        DESCRIPTION_FONT_SIZE = 10
        METHOD_TITLE_FONT_SIZE = 10
        METHOD_BODY_FONT_SIZE = 9
        HEADER_TITLE_FONT_SIZE = 10

        # Padding attorno alle immagini (in punti)
        IMG_HPAD = 14           # padding orizzontale standard (doppiette)
        IMG_HPAD_DEM = 22       # padding orizzontale extra per il DEM singolo
        IMG_VPAD = 6            # padding verticale

        # Fattori di larghezza (percentuale della larghezza utile del frame)
        TARGET_W_ALL = 0.92     # doppiette
        TARGET_W_DEM = 0.88     # DEM leggermente più stretto per compensare la colorbar nel PNG

        # Correzione visiva DEM: spazio vuoto a destra per "compensare" la colorbar
        DEM_RIGHT_SPACER = 200  # punti

        # Tripleta (3 pannelli) - SOLO per i file che contengono "triplet"
        TARGET_W_TRIPLET = 1.00
        TRIPLET_HPAD     = 0
        TRIPLET_MAX_HFR  = None   # nessun cap in altezza per le triplette

        # Documento
        doc = SimpleDocTemplate(
            file_path,
            pagesize=letter,
            leftMargin=18,
            rightMargin=18,
            topMargin=24,
            bottomMargin=24
        )
        elements = []

        # Stili
        styles = getSampleStyleSheet()
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
        styles.add(ParagraphStyle(
            name='TitleSmall',
            parent=styles['Title'],
            fontSize=TITLE_FONT_SIZE,
            alignment=TA_JUSTIFY
        ))

        # Percorsi immagini header
        script_dir = os.path.dirname(os.path.abspath(__file__))
        header_image_path = os.path.join(script_dir, '..', '..', 'frontend', 'public', 'images', 'ingv_Etna.jpg')
        logo_image_path = os.path.join(script_dir, '..', '..', 'frontend', 'public', 'images', 'logo-ingv.jpeg')

        header_image_path = os.path.normpath(header_image_path)
        logo_image_path = os.path.normpath(logo_image_path)

        if not os.path.exists(header_image_path):
            raise FileNotFoundError(f"Immagine di intestazione non trovata nel percorso: {header_image_path}")
        if not os.path.exists(logo_image_path):
            raise FileNotFoundError(f"Logo non trovato nel percorso: {logo_image_path}")

        # Logo + header image
        logo = Image(logo_image_path)
        logo_width = 80
        logo_height = 80
        logo.drawWidth = logo_width
        logo.drawHeight = logo_height

        header_title_text = "Interface for DEM processing"
        etna_with_text = ImageWithText(
            image_path=header_image_path,
            text=header_title_text,
            font_size=HEADER_TITLE_FONT_SIZE,
            text_color=colors.white,
            x=10,
            y=10,
            width=400,
            height=100
        )

        header_table = Table([
            [logo, etna_with_text]
        ], colWidths=[logo_width + 20, 400 + 20])

        header_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 10),
            ('RIGHTPADDING', (0, 0), (-1, -1), 10),
            ('TOPPADDING', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ]))

        elements.append(header_table)
        elements.append(Spacer(1, 12))

        # Titolo
        elements.append(Paragraph(title, styles['TitleSmall']))
        elements.append(Spacer(1, 12))

        # Tabella risultati
        table_data = [["Description", "Value"]]
        for line in (results_list or []):
            if ':' in line:
                description, value = line.split(':', 1)
                table_data.append([description.strip(), value.strip()])
            else:
                table_data.append([line.strip(), ""])

        table = Table(table_data, colWidths=[300, 200])
        style = TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ])
        table.setStyle(style)
        elements.append(table)
        elements.append(Spacer(1, 36))

        # ====== DESCRIZIONI DETTAGLIATE ======
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
                    'Identification of Opposite Points on the Caldera: Using the slope map derived from the DEM, points with the highest slopes on the caldera are identified. Subsequently, points opposite to these maximum slope points along the caldera contour are selected.',
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

        for idx, item in enumerate(descriptions):
            elements.append(Paragraph(item['title'], styles['DescriptionTitle']))
            elements.append(Paragraph(f"<b>Description:</b> {item['description']}", styles['DescriptionBody']))
            elements.append(Paragraph("<b>Method of Calculation:</b>", styles['DescriptionBody']))

            for method in item['method']:
                if isinstance(method, str):
                    if method.strip() and method.strip()[0].isdigit():
                        elements.append(Paragraph(method, styles['MethodBody']))
                    else:
                        elements.append(Paragraph(f"- {method}", styles['MethodBody']))

            # Page break dopo la prima sezione (coerente con il tuo originale)
            if idx == 0:
                elements.append(PageBreak())

            elements.append(Spacer(1, 12))

        # ====== BLOCCHI FIGURE (una dopo l'altra, senza PageBreak forzati) ======
        if image_paths:
            if captions is None or len(captions) != len(image_paths):
                captions = [None] * len(image_paths)

            # Altezza max standard (solo per NON triplette)
            max_h_fraction = 0.36

            for img_path, cap in zip(image_paths, captions):
                if not img_path or not os.path.exists(img_path):
                    continue

                img = Image(img_path)
                base = os.path.basename(img_path).lower()

                # Classificazioni
                is_dem            = ('dem_overview' in base)    # DEM standalone
                is_triplet        = ('triplet' in base)         # TRIPLETTE
                is_final_doublet  = base.startswith('final_doublet_') or ('final_doublet' in base)

                # Larghezza target
                if is_dem:
                    target_w_factor = TARGET_W_DEM
                elif is_triplet:
                    target_w_factor = TARGET_W_TRIPLET
                else:
                    target_w_factor = TARGET_W_ALL

                target_w = doc.width * target_w_factor
                img.drawWidth = target_w
                img.drawHeight = img.imageHeight * (img.drawWidth / float(img.imageWidth))

                # Cap in altezza:
                # - triplette: disattivato
                # - doppiette/DEM: applicato
                if (not is_triplet) and (max_h_fraction is not None):
                    max_draw_h = doc.height * max_h_fraction

                    if is_final_doublet:
                        # Forza SEMPRE la doppietta finale a questa altezza per uniformità
                        if img.drawHeight > 0:
                            scale = max_draw_h / float(img.drawHeight)
                            img.drawWidth *= scale
                            img.drawHeight = max_draw_h
                    else:
                        # comportamento standard per tutte le altre immagini
                        if img.drawHeight > max_draw_h:
                            scale = max_draw_h / float(img.drawHeight)
                            img.drawWidth *= scale
                            img.drawHeight *= scale
                            target_w = img.drawWidth  # aggiorna se scalato

                # Spazio prima dell'immagine
                elements.append(Spacer(1, 12))

                if is_dem:
                    # ---------- DEM standalone: "shift" visivo a sinistra ----------
                    dem_inner = Table(
                        [[img]],
                        colWidths=[doc.width - DEM_RIGHT_SPACER],
                        style=TableStyle([
                            ('LEFTPADDING',  (0, 0), (-1, -1), IMG_HPAD_DEM),
                            ('RIGHTPADDING', (0, 0), (-1, -1), IMG_HPAD_DEM),
                            ('TOPPADDING',   (0, 0), (-1, -1), IMG_VPAD),
                            ('BOTTOMPADDING',(0, 0), (-1, -1), IMG_VPAD),
                            ('ALIGN',        (0, 0), (-1, -1), 'CENTER'),
                            ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
                            ('BOX',          (0, 0), (-1, -1), 0, colors.white),
                        ])
                    )
                    dem_wrapper = Table(
                        [[dem_inner, ""]],
                        colWidths=[doc.width - DEM_RIGHT_SPACER, DEM_RIGHT_SPACER],
                        style=TableStyle([
                            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                            ('BOX',    (0, 0), (-1, -1), 0, colors.white),
                        ])
                    )
                    elements.append(dem_wrapper)

                elif is_triplet:
                    # ---------- TRIPLETTE: niente Table → vera larghezza piena ----------
                    elements.append(img)

                else:
                    # ---------- Doppiette (incl. doppietta finale): centratura "pulita" ----------
                    img_container = Table(
                        [[img]],
                        colWidths=[doc.width],
                        style=TableStyle([
                            ('LEFTPADDING',  (0, 0), (-1, -1), IMG_HPAD),
                            ('RIGHTPADDING', (0, 0), (-1, -1), IMG_HPAD),
                            ('TOPPADDING',   (0, 0), (-1, -1), IMG_VPAD),
                            ('BOTTOMPADDING',(0, 0), (-1, -1), IMG_VPAD),
                            ('ALIGN',        (0, 0), (-1, -1), 'CENTER'),
                            ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
                            ('BOX',          (0, 0), (-1, -1), 0, colors.white),
                        ])
                    )
                    elements.append(img_container)

                # didascalia (opzionale)
                if cap:
                    elements.append(Spacer(1, 6))
                    elements.append(Paragraph(cap, styles['DescriptionBody']))

            # piccolo spazio finale
            elements.append(Spacer(1, 18))

        # Build
        doc.build(elements)
    except Exception as e:
        raise e
