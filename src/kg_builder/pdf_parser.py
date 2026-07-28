import pdfplumber
import json
import os
import re

def parse_pdf_automatically():
    raw_dir = "data/raw"
    processed_dir = "data/processed"
    
    pdf_files = [f for f in os.listdir(raw_dir) if f.endswith('.pdf')]
    if not pdf_files:
        print(f"❌ 오류: '{raw_dir}' 폴더에 PDF 파일이 없습니다.")
        return

    pdf_path = os.path.join(raw_dir, pdf_files[0])
    print(f"📁 자동 파싱 대상 파일: {pdf_path}")
    print("🚀 PDF 자동 추출 및 정제 시작...\n")

    department_name = "컴퓨터공학과"
    graduation_rules = {
        "total_credits": 0,
        "major_required_credits": 0,
        "major_elective_credits": 0,
        "general_credits": 0
    }
    extracted_courses = []
    
    course_id_pattern = re.compile(r'[A-Z]{3}\d{4}')

    with pdfplumber.open(pdf_path) as pdf:
        current_category = "교양"

        for page_idx, page in enumerate(pdf.pages):
            tables = page.extract_tables()
            
            for table in tables:
                for row in table:
                    clean_row = [str(cell).strip() if cell is not None else "" for cell in row]
                    row_text = " ".join(clean_row)

                    # 1. 졸업 배분 규정 파싱
                    if "전공심화" in row_text and "130" in row_text:
                        nums = re.findall(r'\d+', row_text)
                        if len(nums) >= 5:
                            graduation_rules["general_credits"] = int(nums[3])
                            graduation_rules["major_required_credits"] = int(nums[4])
                            graduation_rules["major_elective_credits"] = int(nums[5])
                            graduation_rules["total_credits"] = int(nums[-1])

                    # 2. 카테고리 상태 업데이트 (줄바꿈/공백 제거 전처리)
                    first_cell_normalized = clean_row[0].replace('\n', '').replace(' ', '')
                    if "전공필수" in first_cell_normalized:
                        current_category = "전공필수"
                    elif "전공선택" in first_cell_normalized:
                        current_category = "전공선택"
                    elif "균형교양" in row_text or "확대교양" in row_text:
                        current_category = "교양"

                    # 3. 학수번호 매칭 및 데이터 정제
                    matches = course_id_pattern.findall(row_text)
                    if matches:
                        raw_ids = clean_row[1].split() if len(clean_row) > 1 else []
                        raw_credits = clean_row[3].split() if len(clean_row) > 3 else []
                        
                        # 개설학기 텍스트 정제 (줄바꿈 결합 후 공백 정리)
                        raw_semesters_text = clean_row[6].replace('\n', ' ') if len(clean_row) > 6 else ""
                        raw_semesters = [s for s in raw_semesters_text.split(' ') if s.strip()]

                        # 과목명 줄바꿈 결합 처리
                        lines = clean_row[2].split('\n') if len(clean_row) > 2 else []
                        full_names = []
                        temp_name = ""
                        
                        for line in lines:
                            line_str = line.strip()
                            if line_str:
                                if temp_name and not line_str.startswith("("):
                                    full_names.append(temp_name.strip())
                                    temp_name = line_str
                                else:
                                    temp_name += " " + line_str if temp_name else line_str
                        if temp_name:
                            full_names.append(temp_name.strip())

                        # 최종 리스트 조립
                        for idx, c_id in enumerate(raw_ids):
                            c_name = full_names[idx] if idx < len(full_names) else "과목명 미인식"
                            c_credit = int(raw_credits[idx]) if idx < len(raw_credits) and raw_credits[idx].isdigit() else 3
                            c_sem = raw_semesters[idx] if idx < len(raw_semesters) else ""

                            if not any(c['course_id'] == c_id for c in extracted_courses):
                                extracted_courses.append({
                                    "course_id": c_id,
                                    "course_name": c_name,
                                    "credit": c_credit,
                                    "category": current_category,
                                    "recommended_semester": c_sem
                                })

    final_data = {
        "department": department_name,
        "graduation_rules": graduation_rules,
        "courses": extracted_courses
    }

    # v2 파일명으로 구분하여 저장
    os.makedirs(processed_dir, exist_ok=True)
    output_path = os.path.join(processed_dir, "auto_extracted_rules_v2.json")
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(final_data, f, ensure_ascii=False, indent=2)

    print(f"✅ 파서 실행 완료!")
    print(f"📊 규정: {graduation_rules}")
    print(f"📚 과목 수: {len(extracted_courses)}개")
    print(f"💾 저장 위치: {output_path}")

if __name__ == "__main__":
    parse_pdf_automatically()
