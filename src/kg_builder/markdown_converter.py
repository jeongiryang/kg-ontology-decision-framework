import pymupdf4llm
import os

def convert_pdf_to_markdown():
    raw_dir = "data/raw"
    processed_dir = "data/processed"
    
    pdf_files = [f for f in os.listdir(raw_dir) if f.endswith('.pdf')]
    if not pdf_files:
        print(f"❌ 오류: '{raw_dir}' 폴더에 PDF 파일이 없습니다.")
        return

    pdf_path = os.path.join(raw_dir, pdf_files[0])
    print(f"📁 대상 PDF 파일: {pdf_path}")
    print("🚀 PDF -> Markdown 변환 시작...\n")

    # PyMuPDF4LLM을 이용해 마크다운으로 변환
    md_text = pymupdf4llm.to_markdown(pdf_path)

    os.makedirs(processed_dir, exist_ok=True)
    output_path = os.path.join(processed_dir, "raw_markdown.md")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(md_text)

    print(f"✅ 마크다운 변환 완료!")
    print(f"💾 저장 위치: {output_path}")

if __name__ == "__main__":
    convert_pdf_to_markdown()
