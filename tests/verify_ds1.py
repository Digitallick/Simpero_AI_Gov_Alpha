import shutil
import sys
from pathlib import Path
from hashlib import sha256

# Windows' default console codepage (cp1252) can't encode the emoji used below.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

# Add parser_service to path so we can run standalone
sys.path.append(str(Path(__file__).parent.parent / "services" / "parser"))

from services.parser.parser_service.docling_parser import parse_pdf_bytes


def copy_test_pdf_if_needed() -> Path | None:
    dest_dir = Path(__file__).parent / "test_data"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / "1st-App-H-PTL-Group-CIM.pdf"

    if not dest_path.exists():
        src_path = Path("p:/simpero_GOV_AI/scripts/examples/1st-app-h-ptl/1st-App-H-PTL-Group-CIM.pdf")
        if src_path.exists():
            shutil.copy(src_path, dest_path)

    return dest_path if dest_path.exists() else None

def run_verification():
    print("====================================================")
    print("DS-1 Tester & Evaluation Runner")
    print("====================================================")
    
    # 1. Locate CIM PDF
    print("[1] Locating 1st-App-H-PTL-Group-CIM.pdf...")
    pdf_path = copy_test_pdf_if_needed()
    if not pdf_path or not pdf_path.exists():
        print("❌ Error: 1st-App-H-PTL-Group-CIM.pdf could not be found.")
        print("Please ensure simpero_GOV_AI sibling repository is located at p:/simpero_GOV_AI.")
        sys.exit(1)
    print(f"✅ Found test PDF at: {pdf_path}")
    
    # 2. Reading PDF bytes
    print("[2] Reading PDF bytes...")
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()
    print(f"✅ Read {len(pdf_bytes)} bytes.")
    
    # 3. Running parser
    print("[3] Running Docling parser (this may take a few seconds)...")
    try:
        result = parse_pdf_bytes(pdf_bytes)
    except Exception as e:
        print(f"❌ Parser failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    print("✅ Parser completed successfully.")
    
    # 4. Verifying page count
    print(f"[4] Verifying page count (expected 19)...")
    page_count = len(result.pages)
    if page_count != 19:
        print(f"❌ Error: Expected 19 pages, got {page_count}.")
        sys.exit(1)
    print(f"✅ Page count is {page_count}.")
    
    # 5. Verifying length and character invariants
    print("[5] Verifying page-level char_map length and character invariants...")
    for idx, page in enumerate(result.pages):
        page_no = page.page
        text_len = len(page.text)
        map_len = len(page.char_map)
        if text_len != map_len:
            print(f"❌ Error on Page {page_no}: text length ({text_len}) does not match char_map length ({map_len}).")
            sys.exit(1)
            
        for k in range(text_len):
            if page.text[k] != page.char_map[k].char:
                print(f"❌ Error on Page {page_no} at character index {k}: text has '{page.text[k]}', char_map has '{page.char_map[k].char}'.")
                sys.exit(1)
    print("✅ Page invariants verified successfully for all 19 pages.")
    
    # 6. Verifying numeric token normalization on Page 11
    print("[6] Verifying numeric token normalization on Page 11 (Income Statement)...")
    page_11 = result.pages[10]
    
    # The value 3,817 renders as "3 ,817" in the raw PDF
    if "3,817" not in page_11.text:
        print("❌ Error: Expected normalized '3,817' to be present on Page 11.")
        print("Page 11 snippet:")
        print(page_11.text[:500])
        sys.exit(1)
        
    if "3 ,817" in page_11.text:
        print("❌ Error: Found un-normalized '3 ,817' on Page 11. Normalization did not collapse the space.")
        sys.exit(1)
        
    print("✅ Space-separated numeric token '3 ,817' successfully normalized to '3,817'.")
    
    # 7. Verifying serialized cache file
    print("[7] Verifying DoclingDocument serialization cache...")
    digest = sha256(pdf_bytes).hexdigest()
    cache_file = Path("services/parser/cache") / f"{digest}.json"
    if not cache_file.exists():
        print(f"❌ Error: Serialized cache file {cache_file} was not created.")
        sys.exit(1)
        
    size_kb = cache_file.stat().st_size / 1024
    print(f"✅ Cache file created successfully: {cache_file} ({size_kb:.2f} KB).")
    
    print("\n====================================================")
    print("🎉 ALL TESTS PASSED! DS-1 IS 100% CORRECT & READY!")
    print("====================================================")

if __name__ == "__main__":
    run_verification()
