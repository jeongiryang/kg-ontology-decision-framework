import pdfplumber
import pandas as pd
import json
import os

def parse_pdf_tables():
    raw_dir = "data/raw"
    processed_dir = "data/processed"
    
    # PDF 파일 탐색
    pdf_files = [f for f in os.listdir(raw_dir) if f.endswith('.pdf')]
    if not pdf_files:
        print(f"❌ 오류: '{raw_dir}' 폴더에 PDF 파일이 없습니다.")
        return

    pdf_path = os.path.join(raw_dir, pdf_files[0])
    print(f"📁 파싱 대상 파일: {pdf_path}")

    extracted_data = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            tables = page.extract_tables()
            for table_idx, table in enumerate(tables):
                cleaned_rows = []
                for row in table:
                    # None 치환 및 줄바꿈 정리
                    cleaned_row = [str(cell).replace('\n', ' ').strip() if cell is not None else "" for cell in row]
                    cleaned_rows.append(cleaned_row)
                
                extracted_data.append({
                    "page": page_idx + 1,
                    "table_index": table_idx + 1,
                    "rows": cleaned_rows
                })

    # data/processed/ 에 JSON으로 저장
    os.makedirs(processed_dir, exist_ok=True)
    output_path = os.path.join(processed_dir, "raw_extracted_tables.json")
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(extracted_data, f, ensure_ascii=False, indent=2)

    print(f"✅ 파싱 완료! 결과 저장 위치: {output_path}")

if __name__ == "__main__":
    parse_pdf_tables()
