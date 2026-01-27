# pdf_generator.py

import os
from reportlab.lib.pagesizes import letter
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, PageBreak, Flowable,
    KeepTogether
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
        TARGET_W_ALL = 0.88     # doppiette
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

        # Recupera spazio per far stare 1+2 nella prima pagina
        elements.append(Spacer(1, 12))  # (era 36)

        # ====== DESCRIZIONI DETTAGLIATE (UPDATED, COERENTI CON I NUOVI SVILUPPI) ======
        descriptions = [
            {
                'title': '1. Base Area of the Volcano',
                'description': (
                    "The base area represents the planimetric surface enclosed by the volcano base contour, "
                    "and provides a primary measure of the edifice footprint."
                ),
                'method': [
                    "Identification of the Base Contour: the base contour is extracted at a threshold elevation "
                    "defined as a fixed ratio (e.g., 5%) between the minimum and maximum DEM elevations.",
                    "Area Calculation: the contour polygon is converted to map coordinates using the raster affine transform; "
                    "the area is computed with the shoelace formula (m²) and then reported in km²."
                ]
            },
            {
                'title': '2. Base Width (Distance between Opposite Points of the Base)',
                'description': (
                    "The base width is measured as the distance between two opposite points on the base contour, "
                    "providing a representative span of the edifice footprint."
                ),
                'method': [
                    "Identification of Opposite Points: after defining the base contour, two opposite points are selected "
                    "automatically along the contour geometry.",
                    "Distance Calculation: pixel coordinates are converted to map coordinates via the affine transform and "
                    "the Euclidean distance is computed in meters, then reported in kilometers (km)."
                ]
            },
            {
                'title': '3. Caldera Area of the Volcano',
                'description': (
                    "The caldera area represents the planimetric surface enclosed by the caldera contour, intended to describe "
                    "the extent of the summit depression/feature."
                ),
                'method': [
                    "Identification of the Caldera Contour: the caldera contour is extracted at a high-elevation level "
                    "(e.g., 80% of the DEM maximum elevation).",
                    "Area Calculation: as for the base, the contour is converted to map coordinates and its area is computed "
                    "with the shoelace formula (m²), then reported in km²."
                ]
            },
            {
                'title': '4. Caldera Width (Distance between Opposite Points of the Caldera)',
                'description': (
                    "The caldera width is the distance between two opposite points along the caldera contour, providing a "
                    "representative span of the summit feature."
                ),
                'method': [
                    "Identification of Opposite Points on the Caldera: a slope map derived from the DEM is used to identify a "
                    "maximum-slope point along the caldera contour; the opposite point is then selected approximately halfway "
                    "around the contour.",
                    "Distance Calculation: the span is computed in map units using the affine transform (meters) and reported in km."
                ]
            },
            {
                'title': '5. Total Volume of the Volcanic Edifice',
                'description': (
                    "The total edifice volume is estimated using a frustum-like model based on base and caldera spans "
                    "and a robust DEM-derived height."
                ),
                'method': [
                    "Height Estimation: the edifice height is estimated robustly as P99–P05 of DEM elevations (typically within the "
                    "base mask; fallback to valid DEM values if needed).",
                    "Approximation Model (frustum-like): base and caldera radii are defined as R_base = D_base/2 and R_caldera = D_caldera/2; "
                    "volume is computed as V = (π·h/3)·(R_base² + R_caldera² + R_base·R_caldera).",
                    "Volume Conversion: the resulting volume is computed in m³ and reported in cubic kilometers (km³)."
                ]
            },
            {
                'title': '6. Caldera Volume',
                'description': (
                    "The caldera volume represents the void associated with the summit depression and is treated as a mass-less "
                    "portion when computing the effective edifice volume."
                ),
                'method': [
                    "Rim→DEM depth integration (DEM-based): a reference rim elevation is estimated from robust percentiles sampled around "
                    "the caldera boundary (outer ring, with contour fallback if needed). The void volume is then computed by integrating "
                    "over caldera pixels the positive depth (z_rim − z) multiplied by the pixel area.",
                    "Quality control: if the caldera is classified as complex/non-depressive (e.g., rim below floor), the caldera volume "
                    "may be reported as N/A according to the module output.",
                    "Volume Conversion: the computed volume is in m³ and reported in km³."
                ]
            },
            {
                'title': '7. Effective Volume of the Volcanic Edifice',
                'description': (
                    "The effective volume represents the amount of volcanic material after removing the caldera void, computed as the "
                    "difference between total edifice volume and caldera volume."
                ),
                'method': [
                    "Where:",
                    "- V_total is the total edifice volume estimated with the frustum-like model.",
                    "- V_caldera is the caldera void volume estimated by the selected caldera method.",
                    "Effective volume is computed as V_effective = V_total − V_caldera (consistent metric units)."
                ]
            }
        ]

        # ---------- Costruisco i blocchi delle singole sezioni ----------
        section_blocks = []
        for item in descriptions:
            block = []
            block.append(Paragraph(item['title'], styles['DescriptionTitle']))
            block.append(Paragraph(f"<b>Description:</b> {item['description']}", styles['DescriptionBody']))
            block.append(Paragraph("<b>Method of Calculation:</b>", styles['DescriptionBody']))

            for method in item['method']:
                if isinstance(method, str):
                    if method.strip() and method.strip()[0].isdigit():
                        block.append(Paragraph(method, styles['MethodBody']))
                    else:
                        block.append(Paragraph(f"- {method}", styles['MethodBody']))

            block.append(Spacer(1, 12))
            section_blocks.append(block)

        # ---------- Sezione 1 + Sezione 2 insieme (mai spezzate) ----------
        if len(section_blocks) >= 2:
            first_two_together = []
            first_two_together.extend(section_blocks[0])
            first_two_together.extend(section_blocks[1])
            elements.append(KeepTogether(first_two_together))
        elif len(section_blocks) == 1:
            elements.append(KeepTogether(section_blocks[0]))

        # ---------- Page break PRIMA della Sezione 3 ----------
        if len(section_blocks) >= 3:
            elements.append(PageBreak())

        # ---------- Dalla Sezione 3 in poi normalmente (ogni sezione come blocco unico) ----------
        for blk in section_blocks[2:]:
            elements.append(KeepTogether(blk))  # ⬅️ FIX: non appendere la lista pura!

        # ====== BLOCCHI FIGURE (una dopo l'altra, senza PageBreak forzati) ======
        if image_paths:
            if captions is None or len(captions) != len(image_paths):
                captions = [None] * len(image_paths)

            # Altezza max standard (solo per NON triplette)
            max_h_fraction = 0.34

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