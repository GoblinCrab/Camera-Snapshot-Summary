import os
import json
import logging
import glob
from fpdf import FPDF
from fpdf.enums import XPos, YPos
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime
from config import cfg

logging.basicConfig(filename='log.txt', level=logging.ERROR,
                    format='%(asctime)s - %(levelname)s - %(message)s')


def create_error_image(ch):
    path = f"{cfg.SNAPSHOTS_DIR}/err_{ch}.jpg"
    img = Image.new('RGB', (704, 576), color=(0, 0, 0))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 40)
    except:
        font = ImageFont.load_default()
    text = "Error getting snapshot"
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text(
        ((704 - (bbox[2] - bbox[0])) / 2, (576 - (bbox[3] - bbox[1])) / 2),
        text, fill=(255, 255, 255), font=font
    )
    img.save(path, "JPEG")
    return path


def create_pdf():
    # Clean up previously generated PDFs
    for f in glob.glob("Summary_*.pdf"):
        os.remove(f)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        with open(cfg.MANIFEST_FILE, "r") as f:
            manifest_data = json.load(f)

        for establishment, channels in manifest_data.items():
            chunks = [
                channels[i:i + cfg.PDF_MAX_IMAGES_PER_PDF]
                for i in range(0, len(channels), cfg.PDF_MAX_IMAGES_PER_PDF)
            ]

            for part_num, chunk in enumerate(chunks, 1):
                pdf = FPDF()
                safe_est_name = establishment.replace(' ', '_')

                file_suffix = f"_{safe_est_name}"
                if len(chunks) > 1:
                    file_suffix += f"_Part{part_num}"
                pdf_filename = f"Summary{file_suffix}.pdf"

                for i in range(0, len(chunk), 8):
                    pdf.add_page()

                    pdf.set_font("Helvetica", 'B', 14)
                    header_title = f"{establishment} Camera Snapshots"
                    if len(chunks) > 1:
                        header_title += f" (Part {part_num})"
                    header_title += f" - Page {i // 8 + 1}"
                    pdf.cell(0, 10, header_title, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')

                    pdf.set_font("Helvetica", size=10)
                    pdf.cell(0, 5, f"Generated: {timestamp}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')

                    current_page_channels = chunk[i:i + 8]

                    for index, ch in enumerate(current_page_channels):
                        col, row = index % 2, index // 2
                        x, y = 10 + (col * 95), 40 + (row * 65)

                        pdf.set_xy(x, y - 5)
                        pdf.set_font("Helvetica", size=10)
                        label = ch.split("-")[-1].replace(".jpg", "").replace("_", " ")
                        pdf.cell(90, 5, label, new_x=XPos.RIGHT, new_y=YPos.TOP)

                        img_path = f"{cfg.SNAPSHOTS_DIR}/{ch}"
                        if not os.path.exists(img_path):
                            img_path = create_error_image(ch)

                        pdf.image(img_path, x=x, y=y, w=90, h=55)

                pdf.output(pdf_filename)
                print(f"[OK] Generated {pdf_filename} ({len(chunk)} cameras).")

        # Cleanup temp error images
        for f in os.listdir(cfg.SNAPSHOTS_DIR):
            if f.startswith("err_"):
                os.remove(os.path.join(cfg.SNAPSHOTS_DIR, f))

    except Exception as e:
        logging.error(f"PDF Generation Failed: {str(e)}")
        print(f"[FAIL] PDF Generation: {e}")


if __name__ == "__main__":
    create_pdf()
