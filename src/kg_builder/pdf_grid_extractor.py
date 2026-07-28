import os
import glob
import pdfplumber
import pandas as pd

def get_pdf_path():
    pdf_files = glob.glob("data/raw/*.pdf")
    if not pdf_files:
        raise FileNotFoundError("data/raw/ 디렉토리에 PDF 파일이 존재하지 않습니다.")
    return pdf_files[0]

def extract_cell_based_tables():
    pdf_path = get_pdf_path()
    print(f"📖 셀 사각형(Cell BBox) 기반 정밀 파서 실행: {pdf_path}\n")
    
    extracted_tables = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            # 1. 페이지 내 표 영역 감지
            tables = page.find_tables()
            if not tables:
                continue

            for t_idx, table in enumerate(tables, start=1):
                # 2. pdfplumber의 셀 구조를 직접 추출 (셀 내부 줄바꿈 보존)
                raw_table_data = table.extract()
                if not raw_table_data:
                    continue

                cleaned_matrix = []
                for row in raw_table_data:
                    cleaned_row = []
                    for cell in row:
                        if cell is None:
                            cleaned_row.append("")
                        else:
                            # 핵심: 셀 내부의 줄바꿈(\n)을 띄어쓰기로 통합하여 행 터짐 방지
                            clean_text = " ".join([line.strip() for line in cell.split("\n") if line.strip()])
                            cleaned_row.append(clean_text)
                    
                    # 의미 있는 데이터가 1개라도 존재하는 행만 추가
                    if any(cleaned_row):
                        cleaned_matrix.append(cleaned_row)

                if cleaned_matrix:
                    extracted_tables.append((page_num, t_idx, cleaned_matrix))

    # 3. CSV 파일 저장
    output_rows = []
    for p_num, t_num, matrix in extracted_tables:
        output_rows.append([f"=== PAGE {p_num} TABLE {t_num} ==="])
        output_rows.extend(matrix)
        output_rows.append([]) # 표 구분용 공백 행

    df = pd.DataFrame(output_rows)
    os.makedirs("data/processed", exist_ok=True)
    save_path = "data/processed/generic_tables_parsed.csv"
    df.to_csv(save_path, index=False, header=False, encoding="utf-8-sig")
    
    print(f"✅ 셀 단위 병합 완료! 결과 저장 위치: {save_path}")
    print(f"📄 총 {len(extracted_tables)}개 표 정밀 스캔 완료\n")

if __name__ == "__main__":
    extract_cell_based_tables()
