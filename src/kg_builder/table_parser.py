import re
import pandas as pd

def parse_markdown_tables():
    md_path = "data/processed/raw_markdown.md"
    
    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()

    courses = []
    lines = content.split('\n')
    
    table_row_pattern = re.compile(r'^\|(.*)\|$')
    # 범용 학수번호 패턴: 영문 2~4자리 + 숫자 4자리 (JPA0061, CDA0143, GEA7260 등 모두 매칭)
    code_pattern = re.compile(r'([A-Z]{2,4}\d{4})')
    
    # 병합 셀 상태 추적 변수
    current_category = "미지정"

    for line in lines:
        line_str = line.strip()
        match = table_row_pattern.match(line_str)
        
        if match:
            cells = [cell.strip() for cell in match.group(1).split('|')]
            
            # 1. 셀 내부에서 '이수구분' 키워드가 감지되면 상태 업데이트 (상태 추적)
            for cell in cells:
                clean_cell = re.sub(r'<[^>]+>', '', cell).replace(" ", "").strip()
                if clean_cell in ["전공필수", "전공선택", "필수", "선택", "전공기초", "균형교양", "확대교양", "기초교양", "교양"]:
                    current_category = clean_cell
                    break

            # 2. 학수번호 탐색
            for idx, cell in enumerate(cells):
                code_match = code_pattern.search(cell)
                if code_match:
                    code = code_match.group(1)
                    is_minor_req = "Y" if "※" in line_str else "N"
                    
                    # 학수번호 왼쪽 셀에 이수구분이 명시되어 있다면 해당 값으로 즉시 갱신
                    if idx > 0:
                        left_val = re.sub(r'<[^>]+>', '', cells[idx-1]).replace(" ", "").strip()
                        if left_val in ["전공필수", "전공선택", "필수", "선택", "전공기초", "교양"]:
                            current_category = left_val

                    # 학수번호 오른쪽 셀 데이터 분석 (과목명, 학점, 학기 등)
                    name_parts = []
                    nums = []
                    semester = ""
                    capability = ""
                    
                    for c in cells[idx+1:]:
                        clean_val = re.sub(r'<[^>]+>', '', c).strip()
                        if not clean_val:
                            continue
                        
                        # 개설학기 패턴 (예: 2-2, 1, 2, 1-1,2 등)
                        if re.search(r'^\d(-\d)?(,\d)?$', clean_val) or re.search(r'\d-\d', clean_val):
                            semester = clean_val
                        # 전공능력기반 패턴 (①, ②, ③ 등)
                        elif re.search(r'[①-⑨]', clean_val):
                            capability = clean_val
                        elif clean_val.isdigit():
                            nums.append(clean_val)
                        else:
                            name_parts.append(clean_val)
                    
                    full_name = " ".join(name_parts).replace("※", "").strip()
                    full_name = re.sub(r'\s+', ' ', full_name)
                    
                    credits = nums[0] if len(nums) > 0 else ""
                    lecture = nums[1] if len(nums) > 1 else credits
                    practice = nums[2] if len(nums) > 2 else "0"
                    
                    courses.append({
                        "이수구분": current_category,
                        "학수번호": code,
                        "과목명": full_name,
                        "학점": credits,
                        "강의시수": lecture,
                        "실습시수": practice,
                        "개설학년학기": semester,
                        "전공능력연관성": capability,
                        "부전공필수여부": is_minor_req
                    })
                    break

    df = pd.DataFrame(courses)
    df = df.drop_duplicates(subset=['학수번호'], keep='first')
    
    print(f"✅ 범용 상태추적 파서 실행 완료! 총 {len(df)}개 과목 파싱 완료\n")
    print(df.to_string())
    
    df.to_csv("data/processed/courses_parsed.csv", index=False, encoding="utf-8-sig")

if __name__ == "__main__":
    parse_markdown_tables()
