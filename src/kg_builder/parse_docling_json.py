import os
import sys
import json

PROCESSED_DIR = "data/processed"

def resolve_ref(doc, ref_str):
    """Docling JSON의 $ref 포인터('#/texts/0', '#/tables/2' 등)를 실제 데이터 객체로 변환"""
    if not ref_str or not ref_str.startswith("#/"):
        return None
    
    parts = ref_str.strip("#/").split("/")
    curr = doc
    for p in parts:
        if p.isdigit():
            p = int(p)
        if isinstance(curr, dict) and p in curr:
            curr = curr[p]
        elif isinstance(curr, list) and isinstance(p, int) and p < len(curr):
            curr = curr[p]
        else:
            return None
    return curr

def extract_table_grid(table_node):
    """Docling table 노드에서 행/열 텍스트 행렬(2D Grid)을 순수 텍스트 배열로 복원"""
    grid = table_node.get("data", {}).get("grid", [])
    if not grid:
        return []
    
    matrix = []
    for row in grid:
        row_cells = []
        for cell in row:
            cell_text = cell.get("text", "").strip()
            row_cells.append(cell_text)
        matrix.append(row_cells)
    return matrix

def parse_target_department(target_dept="컴퓨터공학과"):
    # 1. processed 폴더 내 full_structure.json 찾기
    json_files = [f for f in os.listdir(PROCESSED_DIR) if f.endswith("_full_structure.json")]
    if not json_files:
        print(f"❌ '{PROCESSED_DIR}' 폴더에 *_full_structure.json 파일이 없습니다.")
        sys.exit(1)
        
    json_path = os.path.join(PROCESSED_DIR, json_files[0])
    print(f"📖 백만 줄 통합 JSON 로딩 중: {json_files[0]}")
    
    with open(json_path, "r", encoding="utf-8") as f:
        doc = json.load(f)

    print(f"🔍 [{target_dept}] 문맥(Context) 및 관련 표 동적 탐지 시작...\n")

    body_children = doc.get("body", {}).get("children", [])
    
    extracted_result = {
        "department": target_dept,
        "sections": [],
        "tables": []
    }
    
    is_target_context = False

    # 2. 문서 읽기 순서(Reading Order)대로 노드 추적
    for item in body_children:
        ref = item.get("$ref", "")
        node = resolve_ref(doc, ref)
        if not node:
            continue

        # A. 텍스트 노드인 경우 (#/texts/N)
        if "texts" in ref:
            label = node.get("label", "")
            text = node.get("text", "").strip()

            # 타겟 학과 헤더 감지
            if target_dept in text:
                is_target_context = True
                print(f"🎯 타겟 학과 위치 확인: [{text}]")
                
            # 다른 학과/단과대 헤더를 만나면 타겟 문맥 종료
            elif is_target_context and any(kw in text for kw in ["학과", "학부", "대학"]) and target_dept not in text:
                if text.endswith("학과") or text.endswith("학부") or text.endswith("대학"):
                    print(f"🛑 다음 학과/단과대 영역 진입으로 감지 종료: [{text}]")
                    is_target_context = False

            if is_target_context and text:
                extracted_result["sections"].append({
                    "type": label,
                    "text": text
                })

        # B. 표 노드인 경우 (#/tables/N)
        elif "tables" in ref:
            if is_target_context:
                grid_matrix = extract_table_grid(node)
                if grid_matrix:
                    extracted_result["tables"].append({
                        "table_ref": ref,
                        "rows": len(grid_matrix),
                        "cols": len(grid_matrix[0]) if grid_matrix else 0,
                        "grid": grid_matrix
                    })
                    print(f"  📊 {target_dept} 관련 표 추출 성공! (행: {len(grid_matrix)}, 열: {len(grid_matrix[0]) if grid_matrix else 0})")

    # 3. 정제된 단일 학과 JSON 저장
    output_filename = "computer_science_extracted_v5.json"
    output_path = os.path.join(PROCESSED_DIR, output_filename)
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(extracted_result, f, ensure_ascii=False, indent=2)

    print("\n==================================================")
    print(f"✅ [{target_dept}] 데이터 성공적으로 정제 완료!")
    print(f"💾 추출 결과 파일: {output_path}")
    print(f"📌 추출된 텍스트 헤더/본문 수: {len(extracted_result['sections'])}개")
    print(f"📌 추출된 전용 표 수: {len(extracted_result['tables'])}개")
    print("==================================================\n")

if __name__ == "__main__":
    parse_target_department("컴퓨터공학과")
