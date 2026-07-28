import pdfplumber
import json
import os
import re

def parse_pdf_regex():
    raw_dir = "data/raw"
    processed_dir = "data/processed"
    
    pdf_files = [f for f in os.listdir(raw_dir) if f.endswith('.pdf')]
    if not pdf_files:
        print(f"❌ 오류: '{raw_dir}' 폴더에 PDF 파일이 없습니다.")
        return

    pdf_path = os.path.join(raw_dir, pdf_files[0])
    print(f"📁 읽어올 파일: {pdf_path}")
    print("🚀 정규표현식 기반 텍스트 추출 시작...\n")

    extracted_courses = []

    # 정규식 패턴: 학수번호(대문자3+숫자4) + 공백 + 과목명 + 공백 + 학점
    course_pattern = re.compile(r'([A-Z]{3}\d{4})\s+(.+?)\s+(\d)\s+')

    with pdfplumber.open(pdf_path) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            text = page.extract_text() # 표가 아닌 전체 텍스트 추출
            if not text: continue
            
            lines = text.split('\n')
            for line in lines:
                match = course_pattern.search(line)
                if match:
                    course_id = match.group(1)
                    course_name = match.group(2).strip()
                    credit = int(match.group(3))
                    
                    extracted_courses.append({
                        "course_id": course_id,
                        "course_name": course_name,
                        "credit": credit
                    })

    os.makedirs(processed_dir, exist_ok=True)
    output_path = os.path.join(processed_dir, "clean_courses.json")
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(extracted_courses, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 파싱 완료! 총 {len(extracted_courses)}개의 과목을 성공적으로 분리했습니다.")

if __name__ == "__main__":
    parse_pdf_regex()
