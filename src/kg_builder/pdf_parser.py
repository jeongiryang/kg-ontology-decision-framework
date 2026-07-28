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
    
    # 학수번호 패턴 (대문자 3자리 + 숫자 4자리)
    course_id_pattern = re.compile(r'[A-Z]{3}\d{4}')

    with pdfplumber.open(pdf_path) as pdf:
        current_category = "일반"

        for page_idx, page in enumerate(pdf.pages):
            tables = page.extract_tables()
            
            for table in tables:
                for row in table:
                    # None 값 제거 및 줄바꿈 정리
                    clean_row = [str(cell).strip() if cell is not None else "" for cell in row]
                    row_text = " ".join(clean_row)

                    # 1. 졸업 배분 규정 표 자동 파싱 (1~2페이지 표 분석)
                    if "전공심화" in row_text and "130" in row_text:
                        # 숫자만 추출해서 규칙 생성
                        nums = re.findall(r'\d+', row_text)
                        if len(nums) >= 5:
                            graduation_rules["general_credits"] = int(nums[3]) # 교양 소계
                            graduation_rules["major_required_credits"] = int(nums[4]) # 전공 필수
                            graduation_rules["major_elective_credits"] = int(nums[5]) # 전공 선택
                            graduation_rules["total_credits"] = int(nums[-1]) # 졸업 총 학점

                    # 2. 카테고리(이수구분) 상태 업데이트
                    if "전공 필수" in clean_row[0] or "전공필수" in clean_row[0]:
                        current_category = "전공필수"
                    elif "전공 선택" in clean_row[0] or "전공선택" in clean_row[0]:
                        current_category = "전공선택"
                    elif "균형교양" in row_text or "확대교양" in row_text:
                        current_category = "교양"

                    # 3. 학수번호가 존재하는 행(Row) 처리
                    matches = course_id_pattern.findall(row_text)
                    if matches:
                        # Cell 내부에 뭉쳐있는 학수번호, 과목명, 학점 분리 로직
                        raw_ids = clean_row[1].split() if len(clean_row) > 1 else []
                        raw_names = clean_row[2].split('\n') if len(clean_row) > 2 else []
                        raw_credits = clean_row[3].split() if len(clean_row) > 3 else []
                        raw_semesters = clean_row[6].split() if len(clean_row) > 6 else []

                        # 개수가 안 맞거나 줄바꿈으로 쪼개진 경우 보정 연산
                        if len(raw_ids) > 0:
                            # 텍스트 라인 단위 재해석
                            lines = clean_row[2].split('\n')
                            full_names = []
                            temp_name = ""
                            
                            for line in lines:
                                if line.strip():
                                    if temp_name and not line.startswith("("):
                                        full_names.append(temp_name.strip())
                                        temp_name = line
                                    else:
                                        temp_name += " " + line if temp_name else line
                            if temp_name:
                                full_names.append(temp_name.strip())

                            # 추출된 데이터 조립
                            for idx, c_id in enumerate(raw_ids):
                                c_name = full_names[idx] if idx < len(full_names) else "과목명 미인식"
                                c_credit = int(raw_credits[idx]) if idx < len(raw_credits) and raw_credits[idx].isdigit() else 3
                                c_sem = raw_semesters[idx] if idx < len(raw_semesters) else ""

                                # 중복 방지 및 추가
                                if not any(c['course_id'] == c_id for c in extracted_courses):
                                    extracted_courses.append({
                                        "course_id": c_id,
                                        "course_name": c_name.replace('\n', ' ').strip(),
                                        "credit": c_credit,
                                        "category": current_category,
                                        "recommended_semester": c_sem
                                    })

    # 최종 자동 추출 결과 조립
    final_data = {
        "department": department_name,
        "graduation_rules": graduation_rules,
        "courses": extracted_courses
    }

    # 결과 저장
    os.makedirs(processed_dir, exist_ok=True)
    output_path = os.path.join(processed_dir, "auto_extracted_rules.json")
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(final_data, f, ensure_ascii=False, indent=2)

    print(f"✅ 파싱 완료!")
    print(f"📊 추출된 규정: {graduation_rules}")
    print(f"📚 자동 추출된 과목 수: {len(extracted_courses)}개")
    print(f"💾 저장 위치: {output_path}")

if __name__ == "__main__":
    parse_pdf_automatically()
