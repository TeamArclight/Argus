import re
from typing import Any

def simple_chunk(text: str, *, max_size: int = 1200) -> list[dict[str, Any]]:
    """Fallback simple chunker."""
    chunks = []
    offset = 0
    while offset < len(text):
        end = min(offset + max_size, len(text))
        # try to find a natural break near the end
        if end < len(text):
            break_point = text.rfind('\n', offset, end)
            if break_point != -1 and break_point > offset + (max_size // 2):
                end = break_point + 1
            else:
                break_point = text.rfind(' ', offset, end)
                if break_point != -1 and break_point > offset + (max_size // 2):
                    end = break_point + 1
        
        chunk_text = text[offset:end].strip()
        if chunk_text:
            chunks.append({"text": chunk_text, "clause": None, "offset": offset})
        offset = end
    return chunks

def structure_aware_chunk(text: str, *, max_size: int = 1500, min_size: int = 100) -> list[dict[str, Any]]:
    """Structure-aware chunker."""
    if not text:
        return []
    
    # Structural markers: numbered clauses ("Rule 149:", "2.1", "Section 3:"), horizontal rules, double newlines
    # Regex to match potential structural breaks and capture the clause label if present.
    # Group 1: Clause label or just empty if normal break
    pattern = re.compile(
        r'(?:\n|^)(?:(Rule \d+:|Section \d+:|\d+\.\d+(?:\.\d+)*)\s*)?'
        r'((?:\s*-{3,}\s*)|(?:\n\s*\n))'
    )
    
    # Alternatively, let's just split by double newline or HR, and check if the start matches a clause.
    # Wait, the prompt says "split by structural markers: numbered clauses..., horizontal rules, double newlines"
    
    # A better regex for splitting:
    # Look for \n\n or \n--- or \nRule XYZ: or \nSection XYZ: or \n1.2.3 
    
    splits = []
    pattern = re.compile(
        r'(?:\n\s*\n)|'                   # Double newline
        r'(?:\n\s*-{3,}\s*\n)|'           # Horizontal rule
        r'(?:\n(?=(?:Rule \d+:|Section \d+:|\d+\.\d+(?:\.\d+)*\b)))' # Numbered clauses (lookahead)
    )
    
    last_offset = 0
    for match in pattern.finditer(text):
        end = match.start()
        if end > last_offset:
            splits.append((last_offset, end))
        last_offset = match.end()
    
    if last_offset < len(text):
        splits.append((last_offset, len(text)))
        
    if not splits:
        return simple_chunk(text, max_size=max_size)
    
    clause_pattern = re.compile(r'^(Rule \d+:|Section \d+:|\d+\.\d+(?:\.\d+)*)\b')
    
    # Parse splits into initial chunks
    initial_chunks = []
    for start, end in splits:
        chunk_text = text[start:end].strip()
        if not chunk_text:
            continue
            
        m = clause_pattern.match(chunk_text)
        clause = m.group(1) if m else None
        
        initial_chunks.append({
            "text": chunk_text,
            "clause": clause,
            "offset": start
        })
    
    if not initial_chunks:
        return []
        
    # Merge logic
    merged_chunks = []
    current_chunk = initial_chunks[0]
    
    for next_chunk in initial_chunks[1:]:
        combined_len = len(current_chunk["text"]) + len(next_chunk["text"]) + 1 # +1 for newline or space
        if len(current_chunk["text"]) < min_size and combined_len <= max_size:
            current_chunk["text"] = current_chunk["text"] + "\n" + next_chunk["text"]
            # don't override clause if current already has one, or maybe adopt next_chunk's clause?
            if not current_chunk["clause"]:
                current_chunk["clause"] = next_chunk["clause"]
        elif combined_len <= max_size and not next_chunk["clause"]:
            current_chunk["text"] = current_chunk["text"] + "\n" + next_chunk["text"]
        else:
            merged_chunks.append(current_chunk)
            current_chunk = next_chunk
            
    merged_chunks.append(current_chunk)
    
    # Split chunks that are too large using simple_chunk
    final_chunks = []
    for chunk in merged_chunks:
        if len(chunk["text"]) > max_size:
            sub_chunks = simple_chunk(chunk["text"], max_size=max_size)
            for sc in sub_chunks:
                sc["offset"] = chunk["offset"] + sc["offset"] # approx offset
                sc["clause"] = chunk["clause"]
                final_chunks.append(sc)
        else:
            final_chunks.append(chunk)
            
    return final_chunks
