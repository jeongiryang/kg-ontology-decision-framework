import os
import glob
import pdfplumber
import pandas as pd
import re

def get_pdf_path():
    pdf_files = glob.glob("data/raw/*.pdf")
    if not pdf_files:
        raise FileNotFoundError("data/raw/ 디렉토리에 PDF 파일이 존재하지 않습니다.")
    return pdf_files[0]

def extract_stable_grid_tables():
    pdf_path = get_pdf_path()
    print(f"📖 안정형 격자 파서로 PDF 로드: {pdf_path}")
    
    parsed_courses = []
    code_pattern = re.compile(r'([A-Z]{2,4}\d{4})')

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            # 페이지 내 모든 텍스트 단어(Word) 좌표 추출 (x0, top, x1, bottom, text)
            words = page.extract_words()
            if not words:
                continue

            # 학수번호(Anchor)를 포함하는 단어들을 찾아서 행(Row)의 기준 Y좌표 수집
            course_words = [w for w in words if code_pattern.search(w["text"])]
            if not course_words:
                continue

            # 페이지 내 모든 텍스트 라인을 Y좌표 기준으로 그룹화 (오차 3pt 허용)
            # 텍스트들을 행 단위로 묶기
            lines_dict = {}
            for w in words:
                # Y 좌표를 반올림하여 대략적인 행 그룹 생성
                y_key = round(w["top"] / 10) * 10
                # 근접한 Y 좌표 통합
                matched_key = None
                for k in lines_dict.keys():
                    if abs(k - w["top"]) < 5:
                        matched_key = k
                        break
                if matched_key is not None:
                    lines_dict[matched_key].append(w)
                else:
                    lines_dict[w["top"]] = [w]

            # 학수번호가 포함된 행들을 추려냄
            for w in course_words:
                code_match = code_pattern.search(w["text"])
                if not code_match:
                    continue
                
                code = code_match.group(1)
                row_y = w["top"]
                
                # 해당 학수번호의 Y좌표 ± 6pt 범위 내에 있는 모든 단어를 같은 행의 데이터로 수집
                row_words = [word for word in words if abs(word["top"] - row_y) < 7]
                # X좌표 순으로 정렬
                row_words.sort(key=lambda x: x["x0"])

                # X좌표 위치에 따라 칼럼 분류 (대략적인 대학 규정집 표 X축 기준 범위)
                # [0~80]: 이수구분, [80~150]: 학수번호, [150~350]: 과목명, [350~420]: 학점 등...
                cell_bins = {
                    "category": [],
                    "code": [],
                    "name": [],
                    "credits": [],
                    "lecture": [],
                    "practice": [],
                    "semester": [],
                    "capability": []
                }

                for rw in row_words:
                    x = rw["x0"]
                    text = rw["text"]
                    if x < 85:
                        cell_bins["category"].append(text)
                    elif 85 <= x < 145:
                        cell_bins["code"].append(text)
                    elif 145 <= x < 350:
                        cell_bins["name"].append(text)
                    elif 350 <= x < 415:
                        cell_bins["credits"].append(text)
                    elif 415 <= x < 450:
                        cell_bins["lecture"].append(text)
                    elif 450 <= x < 485:
                        cell_bins["practice"].append(text)
                    elif 485 <= x < 540:
                        cell_bins["semester"].append(text)
                    else:
                        cell_bins["capability"].append(text)

                parsed_courses.append({
                    "이수구분": " ".join(cell_bins["category"]),
                    "학수번호": "".join(cell_bins["code"]),
                    "과목명": " ".join(cell_bins["name"]),
                    "학점": "".join(cell_bins["credits"]),
                    "강의시수": "".join(cell_bins["lecture"]),
                    "실습시수": "".join(cell_bins["practice"]),
                    "개설학년학기": " ".join(cell_bins["semester"]),
                    "전공능력연관성": "".join(cell_bins["capability"])
                })

    df = pd.DataFrame(parsed_courses)
    os.makedirs("data/processed", exist_ok=True)
    save_path = "data/processed/courses_stable_parsed.csv"
    df.to_csv(save_path, index=False, encoding="utf-8-sig")
    
    print(f"\n✅ 안정형 파서 실행 완료! 총 {len(df)}개 과목 추출")
    print(f"📁 저장 경로: {save_path}\n")
    print(df.to_string())

if __name__ == "__main__":
    extract_stable_grid_tables()
