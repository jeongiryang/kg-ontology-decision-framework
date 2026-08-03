import os
import sys
import pandas as pd
from docling.document_converter import DocumentConverter

RAW_DIR = "data/raw"
PROCESSED_DIR = "data/processed"

def select_pdf_file():
    """data/raw/ 폴더 내 PDF 목록을 터미널에 띄우고 선택받는 함수"""
    if not os.path.exists(RAW_DIR):
        print(f"❌ '{RAW_DIR}' 디렉토리가 존재하지 않습니다.")
        sys.exit(1)

    # raw 폴더 내 모든 PDF 파일 탐색
    pdf_files = [f for f in os.listdir(RAW_DIR) if f.lower().endswith(".pdf")]
    
    if not pdf_files:
        print(f"❌ '{RAW_DIR}' 폴더에 PDF 파일이 없습니다. 규정집 PDF를 넣어주세요.")
        sys.exit(1)

    print("\n==================================================")
    print("📂 [data/raw] Docling으로 파싱할 규정집 PDF를 선택하세요")
    print("==================================================")
    for idx, filename in enumerate(pdf_files, 1):
        print(f" [{idx}] {filename}")
    print("==================================================\n")

    while True:
        try:
            choice = int(input("👉 번호 입력: "))
            if 1 <= choice <= len(pdf_files):
                selected_filename = pdf_files[choice - 1]
                selected_path = os.path.join(RAW_DIR, selected_filename)
                return selected_path, selected_filename
            else:
                print("⚠️ 목록에 있는 번호를 선택해주세요.")
        except ValueError:
            print("⚠️ 숫자만 입력해주세요.")

def run_extraction():
    # 1. 파일 선택
    pdf_path, filename = select_pdf_file()
    
    # 확장자 제외한 파일명 (예: '2022_curriculum')
    file_stem = os.path.splitext(filename)[0]
    
    print(f"\n🚀 선택된 규정집: {filename}")
    print("⏳ Docling AI 파싱을 시작합니다. (페이지 분량에 따라 몇 분 정도 소요될 수 있습니다)...\n")

    os.makedirs(PROCESSED_DIR, exist_ok=True)

    # 2. Docling 변환 실행 (기본 엔진)
    converter = DocumentConverter()
    result = converter.convert(pdf_path)

    # 3. 전체 문서 마크다운 저장
    md_filename = f"{file_stem}_full_document.md"
    md_output_path = os.path.join(PROCESSED_DIR, md_filename)
    
    with open(md_output_path, "w", encoding="utf-8") as f:
        f.write(result.document.export_to_markdown())
    print(f"📄 전체 문서 마크다운 저장 완료: {md_output_path}")

    # 4. 추출된 표(Table) 개별 CSV 저장
    tables = result.document.tables
    print(f"📊 총 {len(tables)}개의 표 영역을 인지했습니다. CSV로 변환을 진행합니다...")

    for idx, table in enumerate(tables, 1):
        df = table.export_to_dataframe()
        csv_filename = f"{file_stem}_table_{idx}.csv"
        csv_path = os.path.join(PROCESSED_DIR, csv_filename)
        
        # 엑셀/한글 깨짐 방지 utf-8-sig 적용
        df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    print(f"\n✅ [{filename}] 파싱 완료!")
    print(f"💾 결과물 저장 위치: {PROCESSED_DIR}/ ({file_stem}_table_1.csv ~ {file_stem}_table_{len(tables)}.csv)\n")

if __name__ == "__main__":
    run_extraction()