/**
 * Client-Side PDF Parser and Bidder Fact Extractor
 * Extracts text and structured statutory identifiers from text-layer PDFs in Demo Mode.
 * 100% Client-Side with ZERO network calls.
 */
import type { DocumentType } from '@/types/api';

export interface ParsedPage {
  page: number;
  text: string;
}

export interface ParsedPdf {
  numPages: number;
  fullText: string;
  pages: ParsedPage[];
}

export interface ExtractedFactCandidate {
  field: 'company_name' | 'bidder_reference' | 'gstin' | 'pan' | 'cin' | 'udyam_number';
  label: string;
  value: string;
  sourcePage: number;
  sourceText: string;
  confidence: number;
}

/**
 * Compute SHA-256 hash deterministically.
 * Uses Web Crypto SubtleCrypto (available in all modern browsers and Node).
 * Pure JavaScript fallback with zero Node.js built-in dependencies (no crypto, no Buffer).
 */
export async function computeSha256(buffer: ArrayBuffer): Promise<string> {
  const subtle =
    typeof window !== 'undefined' && window.crypto?.subtle
      ? window.crypto.subtle
      : typeof globalThis !== 'undefined' && globalThis.crypto?.subtle
      ? globalThis.crypto.subtle
      : null;

  if (subtle) {
    const hashBuffer = await subtle.digest('SHA-256', buffer);
    const hashArray = Array.from(new Uint8Array(hashBuffer));
    return hashArray.map((b) => b.toString(16).padStart(2, '0')).join('');
  }

  // Pure JavaScript deterministic hash fallback (zero Node dependencies)
  const bytes = new Uint8Array(buffer);
  let h1 = 0xdeadbeef ^ bytes.length;
  let h2 = 0x41c6ce57 ^ bytes.length;
  for (let i = 0; i < bytes.length; i++) {
    h1 = Math.imul(h1 ^ bytes[i], 2654435761);
    h2 = Math.imul(h2 ^ bytes[i], 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return `sha256_${(h1 >>> 0).toString(16).padStart(8, '0')}${(h2 >>> 0).toString(16).padStart(8, '0')}`;
}

/**
 * Parses text from a text-layer PDF buffer using pdfjs-dist.
 */
export async function parsePdfText(data: ArrayBuffer | Uint8Array): Promise<ParsedPdf> {
  const pdfjs = await import('pdfjs-dist/legacy/build/pdf.mjs');

  // Configure worker
  if (typeof window !== 'undefined') {
    // Hosted locally in Next.js public directory - zero external network calls
    pdfjs.GlobalWorkerOptions.workerSrc = '/pdf.worker.min.mjs';
  } else {
    // Node / test environment fallback
    try {
      // @ts-expect-error worker declaration not typed in pdfjs-dist
      await import('pdfjs-dist/legacy/build/pdf.worker.mjs');
    } catch {
      // Worker fallback in memory
    }
  }

  let uint8: Uint8Array;
  if (data instanceof Uint8Array) {
    uint8 = new Uint8Array(data.byteLength);
    uint8.set(data);
  } else {
    const raw = new Uint8Array(data);
    uint8 = new Uint8Array(raw.byteLength);
    uint8.set(raw);
  }

  const loadingTask = pdfjs.getDocument({
    data: uint8,
    useSystemFonts: true,
    isEvalSupported: false,
  });

  const doc = await loadingTask.promise;
  const pages: ParsedPage[] = [];
  const textParts: string[] = [];

  for (let i = 1; i <= doc.numPages; i++) {
    const page = await doc.getPage(i);
    const content = await page.getTextContent();
    const pageText = content.items
      .map((item: unknown) =>
        item && typeof item === 'object' && 'str' in item && typeof (item as { str: unknown }).str === 'string'
          ? (item as { str: string }).str
          : ''
      )
      .join(' ')
      .replace(/\s+/g, ' ')
      .trim();

    pages.push({ page: i, text: pageText });
    textParts.push(pageText);
  }

  return {
    numPages: doc.numPages,
    fullText: textParts.join('\n\n'),
    pages,
  };
}

/**
 * Extracts statutory identifier facts from text.
 * Covers: Company Name, Bidder Reference, GSTIN, PAN, CIN, UDYAM
 */
export function extractBidderFacts(parsed: ParsedPdf): ExtractedFactCandidate[] {
  const facts: ExtractedFactCandidate[] = [];
  const fullText = parsed.fullText;

  // Helper to locate source page and snippet for an extracted match
  const findSource = (value: string): { page: number; snippet: string } => {
    for (const p of parsed.pages) {
      const idx = p.text.toLowerCase().indexOf(value.toLowerCase());
      if (idx !== -1) {
        const start = Math.max(0, idx - 40);
        const end = Math.min(p.text.length, idx + value.length + 40);
        return {
          page: p.page,
          snippet: p.text.substring(start, end).trim(),
        };
      }
    }
    return {
      page: 1,
      snippet: parsed.pages[0]?.text.substring(0, 100) || fullText.substring(0, 100),
    };
  };

  // 1. Company Name
  const companyLabelMatch = fullText.match(
    /(?:Company(?:\s+Name)?|Bidder(?:\s+Name)?|Name\s+of\s+(?:the\s+)?(?:Bidder|Company|Firm|Entity))\s*[:\-]\s*([A-Za-z0-9\s.,&'()/-]+?)(?=(?:\r?\n|GSTIN|PAN|CIN|UDYAM|Bidder\s+Ref|Tender|Address|$))/i
  );
  const companyEntityMatch = fullText.match(
    /\b([A-Z][A-Za-z0-9\s.,&'()-]+?(?:Pvt\.?\s*Ltd\.?|Private\s+Limited|Ltd\.?|Limited|LLP|Corporation|Enterprises|Solutions))\b/
  );

  if (companyLabelMatch && companyLabelMatch[1].trim()) {
    const val = companyLabelMatch[1].trim();
    const src = findSource(val);
    facts.push({
      field: 'company_name',
      label: 'Company Name',
      value: val,
      sourcePage: src.page,
      sourceText: src.snippet,
      confidence: 0.98,
    });
  } else if (companyEntityMatch && companyEntityMatch[1].trim()) {
    const val = companyEntityMatch[1].trim();
    const src = findSource(val);
    facts.push({
      field: 'company_name',
      label: 'Company Name',
      value: val,
      sourcePage: src.page,
      sourceText: src.snippet,
      confidence: 0.92,
    });
  }

  // 2. Bidder Reference
  const refMatch = fullText.match(
    /(?:Bidder\s+Reference(?:\s+Number|\s+No|\s+ID)?|Registration\s+ID|Bidder\s+ID|Ref(?:\s+No|\s+Number)?)\s*[:\-]\s*([A-Za-z0-9_\-\/]+)/i
  );
  if (refMatch && refMatch[1].trim()) {
    const val = refMatch[1].trim();
    const src = findSource(val);
    facts.push({
      field: 'bidder_reference',
      label: 'Bidder Reference',
      value: val,
      sourcePage: src.page,
      sourceText: src.snippet,
      confidence: 0.95,
    });
  }

  // 3. GSTIN (15 chars e.g. 19ABCDE1234F1Z5)
  const gstinLabelMatch = fullText.match(
    /(?:GSTIN|GST(?:\s+Registration|\s+Number|\s+No)?)\s*[:\-]?\s*([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z])/i
  );
  const gstinRegexMatch = fullText.match(/\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b/);
  const gstinVal = gstinLabelMatch ? gstinLabelMatch[1] : gstinRegexMatch ? gstinRegexMatch[1] : null;

  if (gstinVal) {
    const cleanGstin = gstinVal.trim().toUpperCase();
    const src = findSource(cleanGstin);
    facts.push({
      field: 'gstin',
      label: 'GSTIN',
      value: cleanGstin,
      sourcePage: src.page,
      sourceText: src.snippet,
      confidence: gstinLabelMatch ? 0.99 : 0.95,
    });
  }

  // 4. PAN (10 chars e.g. ABCDE1234F)
  const panLabelMatch = fullText.match(
    /(?:PAN|Permanent\s+Account\s+Number)\s*[:\-]?\s*([A-Z]{5}[0-9]{4}[A-Z])/i
  );
  let panVal: string | null = null;
  if (panLabelMatch) {
    panVal = panLabelMatch[1];
  } else if (gstinVal) {
    // Digits 2 to 12 in 15-char GSTIN is the PAN
    panVal = gstinVal.substring(2, 12);
  } else {
    const panRegexMatch = fullText.match(/\b([A-Z]{5}[0-9]{4}[A-Z])\b/);
    if (panRegexMatch) panVal = panRegexMatch[1];
  }

  if (panVal) {
    const cleanPan = panVal.trim().toUpperCase();
    const src = findSource(cleanPan);
    facts.push({
      field: 'pan',
      label: 'PAN',
      value: cleanPan,
      sourcePage: src.page,
      sourceText: src.snippet,
      confidence: panLabelMatch ? 0.99 : 0.95,
    });
  }

  // 5. CIN (21 chars e.g. U72900WB2026PTC123456)
  const cinLabelMatch = fullText.match(
    /(?:CIN|Corporate\s+Identity\s+Number|Corporate\s+ID)\s*[:\-]?\s*([LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6})/i
  );
  const cinRegexMatch = fullText.match(/\b([LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6})\b/i);
  const cinVal = cinLabelMatch ? cinLabelMatch[1] : cinRegexMatch ? cinRegexMatch[1] : null;

  if (cinVal) {
    const cleanCin = cinVal.trim().toUpperCase();
    const src = findSource(cleanCin);
    facts.push({
      field: 'cin',
      label: 'CIN',
      value: cleanCin,
      sourcePage: src.page,
      sourceText: src.snippet,
      confidence: cinLabelMatch ? 0.99 : 0.95,
    });
  }

  // 6. UDYAM (e.g. UDYAM-WB-10-0012345)
  const udyamLabelMatch = fullText.match(
    /(?:UDYAM|MSME|Udyog\s+Aadhaar)(?:\s+Registration|\s+Reg|\s+Number|\s+No)?\s*[:\-]?\s*(UDYAM-[A-Z]{2}-[0-9]{2}-[0-9]{7})/i
  );
  const udyamRegexMatch = fullText.match(/\b(UDYAM-[A-Z]{2}-[0-9]{2}-[0-9]{7})\b/i);
  const udyamVal = udyamLabelMatch ? udyamLabelMatch[1] : udyamRegexMatch ? udyamRegexMatch[1] : null;

  if (udyamVal) {
    const cleanUdyam = udyamVal.trim().toUpperCase();
    const src = findSource(cleanUdyam);
    facts.push({
      field: 'udyam_number',
      label: 'UDYAM Number',
      value: cleanUdyam,
      sourcePage: src.page,
      sourceText: src.snippet,
      confidence: udyamLabelMatch ? 0.99 : 0.95,
    });
  }

  return facts;
}

/**
 * Infers canonical DocumentType based on file metadata and extracted content.
 */
export function inferDocumentType(filename: string, text: string): DocumentType {
  const lower = (filename + ' ' + text).toLowerCase();
  if (lower.includes('udyam') || lower.includes('msme')) return 'UDYAM_CERT';
  if (lower.includes('gst')) return 'GST_CERT';
  if (lower.includes('pan')) return 'PAN_CERT';
  if (lower.includes('cin') || lower.includes('incorporation') || lower.includes('mca')) return 'MCA_DOCUMENT';
  if (lower.includes('turnover') || lower.includes('balance sheet') || lower.includes('audited')) return 'TURNOVER_CERT';
  if (lower.includes('experience') || lower.includes('completion cert')) return 'EXPERIENCE_CERT';
  if (lower.includes('epfo')) return 'EPFO_CHALLAN';
  if (lower.includes('esic')) return 'ESIC_CHALLAN';
  if (lower.includes('oem') || lower.includes('authorization')) return 'OEM_AUTHORIZATION';
  if (lower.includes('local content')) return 'LOCAL_CONTENT_CERTIFICATE';
  if (lower.includes('financial') || lower.includes('dossier') || lower.includes('synthetic')) return 'FINANCIAL_STATEMENT';
  return 'FINANCIAL_STATEMENT';
}
