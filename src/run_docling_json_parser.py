import os
import sys
import json
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
        print(f"❌ '{RAW_DIR}' 폴더에 PDF 파일이 없습니다. 규정집 PDF를 넣고 다시 실행해주세요.")
        sys.exit(1)

    print("\n==================================================")
    print("📂 [data/raw] 단일 계층형 JSON으로 파싱할 PDF를 선택하세요")
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
                print("⚠️ 목록에 있는 번호를 입력해주세요.")
        except ValueError:
            print("⚠️ 숫자만 입력해주세요.")

def run_json_extraction():
    # 1. 파일 선택
    pdf_path, filename = select_pdf_file()
    file_stem = os.path.splitext(filename)[0]

    print(f"\n🚀 선택된 규정집: {filename}")
    print("⏳ Docling 비전 AI가 계층형 JSON 변환을 시작합니다. (페이지 분량에 따라 2~5분 소요)...\n")

    os.makedirs(PROCESSED_DIR, exist_ok=True)

    # 2. Docling 변환기 실행 (기본 비전 레이아웃 + 표 구조 추론)
    converter = DocumentConverter()
    result = converter.convert(pdf_path)

    # 3. 문맥(제목, 본문, 표 좌표/병합)이 연결된 Native 딕셔너리로 추출
    doc_dict = result.document.export_to_dict()

    # 4. 단일 JSON 파일로 저장
    output_path = os.path.join(PROCESSED_DIR, f"{file_stem}_full_structure.json")
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(doc_dict, f, ensure_ascii=False, indent=2)

    print("\n==================================================")
    print("✅ 단일 JSON 파일 변환 완료!")
    print(f"💾 생성된 파일 경로: {output_path}")
    print("==================================================\n")

if __name__ == "__main__":
    run_json_extraction()