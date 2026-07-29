import pandas as pd
import json
import os
import re

def safe_int(val):
    """문자열('4주', '3.0', NaN 등)에서 숫자만 안전하게 추출하는 함수"""
    if pd.isna(val):
        return 0
    val_str = str(val).strip()
    nums = re.findall(r'\d+', val_str)
    return int(nums[0]) if nums else 0

def compile_v4():
    print("🚀 Docling CSV 데이터를 온톨로지 스펙(V4)에 맞게 조립합니다...\n")

    processed_dir = "data/processed"
    df1 = pd.read_csv(f"{processed_dir}/docling_table_1.csv") # 학점구조표 (졸업요건)
    df4 = pd.read_csv(f"{processed_dir}/docling_table_4.csv") # 학과지정교과목 (교양)
    df5 = pd.read_csv(f"{processed_dir}/docling_table_5.csv") # 전공교육과정표 (전필)
    df6 = pd.read_csv(f"{processed_dir}/docling_table_6.csv") # 전공교육과정표 (전선)

    # 1. 졸업 요건 규정 파싱 (전공심화 + 복수전공)
    graduation_rules = {}
    for idx, row in df1.iterrows():
        major_type = str(row.get('구분', '')).strip()
        if not major_type or major_type == 'nan':
            continue
        graduation_rules[major_type] = {
            "total_credits": safe_int(row['졸업 학점.']),
            "major_required_credits": safe_int(row['전공.전공 필수']),
            "major_elective_credits": safe_int(row['전공.전공 선택']),
            "general_credits": safe_int(row['교양.소계']),
            "general_basic_credits": safe_int(row['교양.기초 교양']),
            "general_balance_credits": safe_int(row['교양.균형 교양']),
            "graduation_remainder_credits": safe_int(row['졸업 잔여 학점.'])
        }

    # 2. 과목 리스트 파싱
    courses = []
    
    # 2-1. 교양 과목 조립
    for idx, row in df4.iterrows():
        c_sub = str(row['구분 (영역)']).strip()
        
        # 교양 영역명 깨짐 정제
        if "자연" in c_sub or "기술" in c_sub:
            c_sub = "균형교양 (4.자연·과학·기술의 이해)"
        elif "디지털" in c_sub:
            c_sub = "균형교양 (1.디지털커뮤니케이션)"
        elif "소양" in c_sub:
            c_sub = "확대교양 (2.소양교육)"

        courses.append({
            "course_id": str(row['학수번호']).strip(),
            "course_name": str(row['과목명']).strip(),
            "credit": safe_int(row['학점']),
            "category": "교양",
            "sub_category": c_sub,
            "lecture_hours": safe_int(row['시수']),
            "practice_hours": 0,
            "recommended_semester": str(row['개설학기']).strip(),
            "competencies": []
        })
        
    # 2-2. 전공 과목 조립 함수 (전필/전선)
    def parse_major_table(df, category):
        course_name_col = [c for c in df.columns if '과' in c and '명' in c][0]

        for idx, row in df.iterrows():
            c_id = str(row['학수번호.학수번호']).strip()
            if not c_id or c_id == 'nan':
                continue

            # 시수 정제 ('4주' 등 안전 파싱)
            l_hours = safe_int(row.get('시간수.강의', 0))
            p_hours = safe_int(row.get('시간수.실습 실기', 0))
                
            # 전공능력 배열 변환 (예: "①③④" -> ["①", "③", "④"])
            comp_str = str(row.get('전공능력 기반 연관성.전공능력 기반 연관성', ''))
            comp_list = [c for c in comp_str if c in "①②③④⑤"]
            
            # 과목명 기호(※) 및 공백 정제
            c_name = str(row[course_name_col]).strip().replace(" ※", "").replace("※ ", "").replace("※", "")
            
            # 개설학기 문구 공백 정제 ("3,4-계 절" -> "3,4-계절", "전학년 - 1,2" -> "전학년-1,2")
            sem_str = str(row['개설 학년 학기.개설 학년 학기']).strip()
            sem_str = sem_str.replace("계 절", "계절").replace(" - ", "-").replace(" -", "-").replace("- ", "-")

            courses.append({
                "course_id": c_id,
                "course_name": c_name,
                "credit": safe_int(row['학점.학점']),
                "category": category,
                "sub_category": "",
                "lecture_hours": l_hours,
                "practice_hours": p_hours,
                "recommended_semester": sem_str,
                "competencies": comp_list
            })

    parse_major_table(df5, "전공필수")
    parse_major_table(df6, "전공선택")

    # 3. 최종 JSON 저장
    final_data = {
        "department": "컴퓨터공학과",
        "graduation_rules": graduation_rules,
        "courses": courses
    }

    output_path = f"{processed_dir}/auto_extracted_rules_v4.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(final_data, f, ensure_ascii=False, indent=2)
        
    print(f"✅ 완벽한 V4 JSON 조립 성공! (총 추출된 과목 수: {len(courses)}개)")
    print(f"💾 확인 경로: {output_path}\n")

if __name__ == "__main__":
    compile_v4()
