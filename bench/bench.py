# Simple offline bench to estimate parsing speed using the same logic
# (It bypasses RabbitMQ and DB for quick iteration.)

import time, decimal

def detect_format(text: str) -> str:
    head = text.strip()[:3].upper()
    if head == "ISA":
        return "X12"
    if text.strip().startswith("UNB") or text.strip().startswith("UNH"):
        return "EDIFACT"
    return "UNKNOWN"

def parse_x12_837(text: str):
    seg_term = "~"
    elem_sep = "*"
    if text.startswith("ISA"):
        elem_sep = text[3]
    segments = [s for s in text.replace("\r","").split(seg_term) if s.strip()]
    count = sum(1 for s in segments if s.startswith("CLM"+elem_sep))
    return count

def parse_edifact_orders(text: str):
    segments = [s for s in text.replace("\r","").split(\"'\") if s.strip()]
    count = sum(1 for s in segments if s.startswith("LIN+"))
    return count

if __name__ == "__main__":
    import sys, pathlib
    p = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path("samples/x12_837_small.txt")
    data = p.read_text(encoding="utf-8", errors="replace")
    t0 = time.perf_counter()
    ftype = detect_format(data)
    if ftype == "X12":
        c = parse_x12_837(data)
        kind = "claims"
        n = c
    elif ftype == "EDIFACT":
        c = parse_edifact_orders(data)
        kind = "order lines"
        n = c
    else:
        kind = "unknown"
        n = 0
    elapsed = time.perf_counter() - t0
    size_kb = len(data.encode("utf-8")) / 1024
    print(f"Parsed {n} {kind} from {p.name} ({size_kb:.1f} KB) in {elapsed*1000:.2f} ms")
