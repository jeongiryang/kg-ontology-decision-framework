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
    print(f"📁 대상 파일: {pdf_path}")
    print("🚀 PDF 자동 추출 및 정제 실행 중...\n")

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

        for page in pdf.pages:
            tables = page.extract_tables()
            
            for table in tables:
                for row in table:
                    clean_row = [str(cell).strip() if cell is not None else "" for cell in row]
                    row_text = " ".join(clean_row)

                    # 1. 졸업 배분 규정 표 파싱
                    if "전공심화" in row_text and "130" in row_text:
                        nums = re.findall(r'\d+', row_text)
                        if len(nums) >= 5:
                            graduation_rules["general_credits"] = int(nums[3])
                            graduation_rules["major_required_credits"] = int(nums[4])
                            graduation_rules["major_elective_credits"] = int(nums[5])
                            graduation_rules["total_credits"] = int(nums[-1])

                    # 2. 카테고리 상태 업데이트
                    if "전공 필수" in row_text or "전공필수" in row_text:
                        current_category = "전공필수"
                    elif "전공 선택" in row_text or "전공선택" in row_text:
                        current_category = "전공선택"
                    elif "균형교양" in row_text or "확대교양" in row_text:
                        current_category = "교양"

                    # 3. 학수번호 행 처리
                    matches = course_id_pattern.findall(row_text)
                    if matches:
                        raw_ids = matches
                        
                        name_cell = clean_row[2] if len(clean_row) > 2 else ""
                        credit_cell = clean_row[3] if len(clean_row) > 3 else ""
                        sem_cell = clean_row[6] if len(clean_row) > 6 else (clean_row[5] if len(clean_row) > 5 else "")

                        # 학기 문구 보정
                        sem_cell_clean = sem_cell.replace("\n", " ").replace("계 절", "계절").replace("- ", "-")
                        sem_cell_clean = re.sub(r'전학년-\s*1,2', '전학년-1,2', sem_cell_clean)
                        
                        raw_credits = re.findall(r'\b\d+\b', credit_cell)
                        raw_semesters = [s.strip() for s in sem_cell_clean.split() if s.strip()]

                        # 과목명 추출
                        lines = [l.strip() for l in name_cell.split('\n') if l.strip()]
                        full_names = []
                        temp_name = ""
                        for line in lines:
                            if temp_name and not line.startswith("("):
                                full_names.append(temp_name.strip())
                                temp_name = line
                            else:
                                temp_name += " " + line if temp_name else line
                        if temp_name:
                            full_names.append(temp_name.strip())

                        # 최종 조립
                        for idx, c_id in enumerate(raw_ids):
                            c_name = full_names[idx] if idx < len(full_names) else "과목명 미인식"
                            c_credit = int(raw_credits[idx]) if idx < len(raw_credits) and raw_credits[idx].isdigit() else 3
                            c_sem = raw_semesters[idx] if idx < len(raw_semesters) else ""

                            if c_id.startswith("GEA"):
                                item_category = "교양"
                            else:
                                item_category = current_category if current_category != "교양" else "전공"

                            if not any(c['course_id'] == c_id for c in extracted_courses):
                                extracted_courses.append({
                                    "course_id": c_id,
                                    "course_name": c_name,
                                    "credit": c_credit,
                                    "category": item_category,
                                    "recommended_semester": c_sem
                                })

    final_data = {
        "department": department_name,
        "graduation_rules": graduation_rules,
        "courses": extracted_courses
    }

    os.makedirs(processed_dir, exist_ok=True)
    # v3 적용한 저장 경로
    output_path = os.path.join(processed_dir, "auto_extracted_rules_v3.json")
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(final_data, f, ensure_ascii=False, indent=2)

    print(f"✅ 파싱 성공!")
    print(f"📚 정밀 추출된 과목 수: {len(extracted_courses)}개")
    print(f"💾 저장 위치: {output_path}\n")

if __name__ == "__main__":
    parse_pdf_automatically()
