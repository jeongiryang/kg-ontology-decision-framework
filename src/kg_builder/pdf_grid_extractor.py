import os
import glob
import pdfplumber
import pandas as pd

def get_pdf_path():
    pdf_files = glob.glob("data/raw/*.pdf")
    if not pdf_files:
        raise FileNotFoundError("data/raw/ 디렉토리에 PDF 파일이 존재하지 않습니다.")
    return pdf_files[0]

def extract_grid_tables():
    pdf_path = get_pdf_path()
    print(f"📖 PDF 파일 발견 및 로드: {pdf_path}")
    
    all_parsed_rows = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            edges = page.edges
            if not edges:
                continue

            v_lines = sorted(list(set([round(e["x0"], 1) for e in edges if e["orientation"] == "v"] + 
                                      [round(e["x1"], 1) for e in edges if e["orientation"] == "v"])))
            h_lines = sorted(list(set([round(e["top"], 1) for e in edges if e["orientation"] == "h"] + 
                                      [round(e["bottom"], 1) for e in edges if e["orientation"] == "h"])))

            def cluster_coords(coords, tolerance=3):
                if not coords:
                    return []
                clusters = [[coords[0]]]
                for c in coords[1:]:
                    if c - clusters[-1][-1] <= tolerance:
                        clusters[-1].append(c)
                    else:
                        clusters.append([c])
                return [sum(cl) / len(cl) for cl in clusters]

            v_grid = cluster_coords(v_lines)
            h_grid = cluster_coords(h_lines)

            if len(v_grid) < 4 or len(h_grid) < 3:
                continue

            for i in range(len(h_grid) - 1):
                row_data = []
                top, bottom = h_grid[i], h_grid[i+1]
                if bottom - top < 5:
                    continue

                for j in range(len(v_grid) - 1):
                    left, right = v_grid[j], v_grid[j+1]
                    try:
                        cropped = page.crop((left, top, right, bottom))
                        text = cropped.extract_text() or ""
                        row_data.append(text.replace("\n", " ").strip())
                    except Exception:
                        row_data.append("")

                if any(row_data):
                    all_parsed_rows.append(row_data)

    df = pd.DataFrame(all_parsed_rows)
    os.makedirs("data/processed", exist_ok=True)
    save_path = "data/processed/grid_scan_preview.csv"
    df.to_csv(save_path, index=False, header=False, encoding="utf-8-sig")
    print(f"✅ 스캔 결과 저장 완료! 파일 경로: {save_path}\n")

if __name__ == "__main__":
    extract_grid_tables()
