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
    print(f"📖 점선/텍스트 행 분리 옵션 적용 파서 실행: {pdf_path}\n")
    
    extracted_tables = []

    # 점선 및 텍스트 줄바꿈 간격을 가로 행 구분선으로 인식시키는 핵심 옵션
    table_settings = {
        "vertical_strategy": "lines",       # 세로선: 실선 그래픽 기준
        "horizontal_strategy": "text",      # 가로선: 텍스트 줄바꿈 간격 기준 (점선 영역 분리)
        "snap_tolerance": 3,
        "join_tolerance": 3,
        "edge_min_length": 3,
    }

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            # 행 분리 옵션을 전달하여 표 감지
            tables = page.find_tables(table_settings=table_settings)
            if not tables:
                continue

            for t_idx, table in enumerate(tables, start=1):
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
                            # 셀 내부의 단순 줄바꿈은 공백 1개로 합쳐 글자 깨짐 방지
                            clean_text = " ".join([line.strip() for line in cell.split("\n") if line.strip()])
                            cleaned_row.append(clean_text)
                    
                    if any(cleaned_row):
                        cleaned_matrix.append(cleaned_row)

                if cleaned_matrix:
                    extracted_tables.append((page_num, t_idx, cleaned_matrix))

    # CSV 파일 저장
    output_rows = []
    for p_num, t_num, matrix in extracted_tables:
        output_rows.append([f"=== PAGE {p_num} TABLE {t_num} ==="])
        output_rows.extend(matrix)
        output_rows.append([])

    df = pd.DataFrame(output_rows)
    os.makedirs("data/processed", exist_ok=True)
    save_path = "data/processed/generic_tables_parsed.csv"
    df.to_csv(save_path, index=False, header=False, encoding="utf-8-sig")
    
    print(f"✅ 행 해체 완료! 결과 저장 위치: {save_path}")
    print(f"📄 총 {len(extracted_tables)}개 표 구역 정밀 파싱 완료\n")

if __name__ == "__main__":
    extract_cell_based_tables()
