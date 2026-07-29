import os
import glob
import json
from docling.document_converter import DocumentConverter

def get_pdf_path():
    pdf_files = glob.glob("data/raw/*.pdf")
    if not pdf_files:
        raise FileNotFoundError("data/raw/ 디렉토리에 PDF 파일이 존재하지 않습니다.")
    return pdf_files[0]

def run_docling_full_parse():
    pdf_path = get_pdf_path()
    print(f"🤖 Docling AI 로컬 모델을 통한 PDF 정밀 파싱 시작: {pdf_path}")
    print("⏳ (첫 실행 시 비전 파싱 모델 다운로드로 수 초 소요될 수 있습니다...)\n")

    converter = DocumentConverter()
    result = converter.convert(pdf_path)

    os.makedirs("data/processed", exist_ok=True)

    # 1. 문서 전체의 마크다운(Markdown) 표현 추출 (표가 구조화된 마크다운 표로 변환됨)
    md_content = result.document.export_to_markdown()
    md_save_path = "data/processed/docling_full_document.md"
    with open(md_save_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"📄 마크다운 추출 완료: {md_save_path}")

    # 2. 추출된 모든 표(Tables) 데이터 살펴보기
    tables = result.document.tables
    print(f"📊 총 감지된 표 개수: {len(tables)}개\n")

    parsed_tables_summary = []
    for idx, table in enumerate(tables, start=1):
        df = table.export_to_dataframe()
        csv_path = f"data/processed/docling_table_{idx}.csv"
        df.to_csv(csv_path, index=False, encoding="utf-8-sig")
        print(f"   - [표 {idx}] CSV 저장: {csv_path} (크기: {df.shape[0]}행 x {df.shape[1]}열)")

if __name__ == "__main__":
    run_docling_full_parse()
