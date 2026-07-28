import re
import pandas as pd

def parse_markdown_table():
    md_path = "data/processed/raw_markdown.md"
    
    with open(md_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    courses = []
    # 마크다운 표의 행(Row)을 찾는 정규식 (| 로 시작하고 끝나는 줄)
    table_row_pattern = re.compile(r'^\|(.*)\|$')

    for line in lines:
        match = table_row_pattern.match(line.strip())
        if match:
            # 셀(Cell) 단위로 쪼개기
            cells = [cell.strip() for cell in match.group(1).split('|')]
            
            # 셀 중 학수번호 패턴(예: CDA0143)이 있는지 검색
            for idx, cell in enumerate(cells):
                if re.match(r'^[A-Z]{3}\d{4}$', cell):
                    code = cell
                    name = cells[idx+1] if idx+1 < len(cells) else ""
                    credits = cells[idx+2] if idx+2 < len(cells) else ""
                    
                    # 과목명 정제 (<br>, ※ 등 잡음 기호 제거)
                    name = name.replace("<br>", " ").replace("※", "").strip()
                    # 연속된 공백 하나로 축소
                    name = re.sub(r'\s+', ' ', name)
                    
                    courses.append({
                        "학수번호": code,
                        "과목명": name,
                        "학점": credits
                    })
                    break

    # Pandas DataFrame 변환
    df = pd.DataFrame(courses)
    print(f"✅ 총 {len(df)}개의 과목을 파이썬 정규식으로 100% 정적 파싱했습니다.\n")
    print(df.to_string())
    
    # CSV 저장
    df.to_csv("data/processed/courses_parsed.csv", index=False, encoding="utf-8-sig")

if __name__ == "__main__":
    parse_markdown_table()
