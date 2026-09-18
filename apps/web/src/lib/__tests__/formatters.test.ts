import { describe, it } from 'node:test';
import assert from 'node:assert';
import {
  formatDisplayValue,
  formatExpectedCondition,
  formatCurrencyINR,
  isStructuredValue,
} from '../formatters.ts';
import { parseAuditDate, formatAuditDate } from '../audit-helpers.tsx';

describe('formatDisplayValue', () => {
  it('handles null and undefined with default and custom fallback', () => {
    const resNull = formatDisplayValue(null);
    assert.strictEqual(resNull, 'Not available');
    assert.ok(!resNull.includes('[object Object]'));

    const resUndef = formatDisplayValue(undefined, { fallback: '—' });
    assert.strictEqual(resUndef, '—');
    assert.ok(!resUndef.includes('[object Object]'));
  });

  it('handles strings safely', () => {
    const res = formatDisplayValue('29ABCDE5678K1Z1');
    assert.strictEqual(res, '29ABCDE5678K1Z1');
    assert.ok(!res.includes('[object Object]'));

    const resEmpty = formatDisplayValue('   ', { fallback: 'None' });
    assert.strictEqual(resEmpty, 'None');
  });

  it('handles plain non-currency numbers without turning them into currency', () => {
    const page = formatDisplayValue(3);
    assert.strictEqual(page, '3');
    assert.ok(!page.includes('₹'));

    const expYears = formatDisplayValue(5);
    assert.strictEqual(expYears, '5');
    assert.ok(!expYears.includes('₹'));

    const count = formatDisplayValue(123456);
    assert.strictEqual(count, '123,456');
    assert.ok(!count.includes('₹'));
    assert.ok(!count.includes('[object Object]'));
  });

  it('handles numbers with explicit currency context', () => {
    const turnover = formatDisplayValue(85000000, { kind: 'currency' });
    assert.strictEqual(turnover, '₹8.5 crore');
    assert.ok(!turnover.includes('[object Object]'));

    const smallAmount = formatDisplayValue(50000, { kind: 'currency' });
    assert.strictEqual(smallAmount, '₹50,000');
  });

  it('handles booleans cleanly', () => {
    assert.strictEqual(formatDisplayValue(true), 'Yes');
    assert.strictEqual(formatDisplayValue(false), 'No');
  });

  it('handles arrays of strings', () => {
    const res = formatDisplayValue(['ISO-9001', 'ISO-27001', 'CMMI-5']);
    assert.strictEqual(res, 'ISO-9001, ISO-27001, CMMI-5');
    assert.ok(!res.includes('[object Object]'));
  });

  it('handles arrays of objects without creating [object Object]', () => {
    const arr = [
      { name: 'GST', status: 'ACTIVE' },
      { name: 'PAN', status: 'VERIFIED' },
    ];
    const res = formatDisplayValue(arr);
    assert.ok(!res.includes('[object Object]'));
    assert.ok(res.includes('ACTIVE'));
    assert.ok(res.includes('VERIFIED'));
  });

  it('handles claimed + verified production GST object', () => {
    const gstObj = {
      claimed: '29ABCDE5678K1Z1',
      verified: {
        status: 'ACTIVE',
        verified_entity: 'Surya Tech Energy Solutions Pvt Ltd',
      },
    };
    const res = formatDisplayValue(gstObj);
    assert.strictEqual(res, '29ABCDE5678K1Z1 · ACTIVE');
    assert.ok(!res.includes('[object Object]'));
  });

  it('handles normalized_value object (turnover)', () => {
    const obj = {
      normalized_value: 116000000,
      display_value: '₹11.6 crore',
    };
    const res = formatDisplayValue(obj);
    assert.strictEqual(res, '₹11.6 crore');
    assert.ok(!res.includes('[object Object]'));
  });

  it('handles arbitrary nested objects safely', () => {
    const nested = {
      audit: {
        score: 98,
        nested_meta: {
          sub_system: 'SCADA',
        },
      },
      status: 'VERIFIED',
    };
    const res = formatDisplayValue(nested);
    assert.ok(!res.includes('[object Object]'));
  });
});

describe('formatExpectedCondition', () => {
  it('formats EXISTS operator with true as Required', () => {
    const res = formatExpectedCondition('EXISTS', true);
    assert.strictEqual(res, 'Required');
  });

  it('formats monetary GTE condition with Lakh/Crore', () => {
    const res = formatExpectedCondition('GTE', 85000000, {
      field: 'financial.average_annual_turnover',
      unit: 'INR',
    });
    assert.strictEqual(res, '≥ ₹8.5 crore');
    assert.ok(!res.includes('[object Object]'));
  });

  it('formats non-monetary GTE condition with years', () => {
    const res = formatExpectedCondition('GTE', 5, {
      field: 'experience.years',
      unit: 'years',
    });
    assert.strictEqual(res, '≥ 5 years');
    assert.ok(!res.includes('₹'));
    assert.ok(!res.includes('[object Object]'));
  });

  it('formats non-monetary GTE condition with projects', () => {
    const res = formatExpectedCondition('GTE', 3, {
      field: 'project_count',
      unit: 'projects',
    });
    assert.strictEqual(res, '≥ 3 projects');
    assert.ok(!res.includes('₹'));
    assert.ok(!res.includes('[object Object]'));
  });
});

describe('formatCurrencyINR', () => {
  it('formats crores, lakhs, and thousands', () => {
    assert.strictEqual(formatCurrencyINR(85000000), '₹8.5 crore');
    assert.strictEqual(formatCurrencyINR(116000000), '₹11.6 crore');
    assert.strictEqual(formatCurrencyINR(500000), '₹5 lakh');
    assert.strictEqual(formatCurrencyINR(250000), '₹2.5 lakh');
    assert.strictEqual(formatCurrencyINR(5000), '₹5,000');
  });
});

describe('isStructuredValue', () => {
  it('identifies objects and arrays vs primitives', () => {
    assert.strictEqual(isStructuredValue({ a: 1 }), true);
    assert.strictEqual(isStructuredValue([1, 2]), true);
    assert.strictEqual(isStructuredValue(null), false);
    assert.strictEqual(isStructuredValue(undefined), false);
    assert.strictEqual(isStructuredValue('hello'), false);
    assert.strictEqual(isStructuredValue(123), false);
    assert.strictEqual(isStructuredValue(true), false);
  });
});

describe('parseAuditDate and formatAuditDate', () => {
  it('safely treats naive UTC ISO string as UTC rather than local time', () => {
    const naiveIso = '2026-09-18T20:53:21.411800';
    const parsed = parseAuditDate(naiveIso);
    assert.ok(parsed !== null);
    assert.strictEqual(parsed.getUTCFullYear(), 2026);
    assert.strictEqual(parsed.getUTCMonth(), 8); // 0-indexed September
    assert.strictEqual(parsed.getUTCDate(), 18);
    assert.strictEqual(parsed.getUTCHours(), 20);
    assert.strictEqual(parsed.getUTCMinutes(), 53);
  });

  it('handles space-separated date and time strings as UTC', () => {
    const spaceSep = '2026-09-18 20:53:21.411800';
    const parsed = parseAuditDate(spaceSep);
    assert.ok(parsed !== null);
    assert.strictEqual(parsed.getUTCHours(), 20);
    assert.strictEqual(parsed.getUTCMinutes(), 53);
  });

  it('handles explicit Z and timezone offset without alteration', () => {
    const withZ = '2026-09-18T20:53:21.411800Z';
    const parsedZ = parseAuditDate(withZ);
    assert.ok(parsedZ !== null);
    assert.strictEqual(parsedZ.getUTCHours(), 20);

    const withOffset = '2026-09-19T02:23:21+05:30';
    const parsedOffset = parseAuditDate(withOffset);
    assert.ok(parsedOffset !== null);
    assert.strictEqual(parsedOffset.getUTCHours(), 20);
    assert.strictEqual(parsedOffset.getUTCMinutes(), 53);
  });

  it('handles invalid or empty strings gracefully with fallback', () => {
    assert.strictEqual(parseAuditDate(null), null);
    assert.strictEqual(parseAuditDate('—'), null);
    assert.strictEqual(parseAuditDate('invalid'), null);
    assert.strictEqual(formatAuditDate(null), '—');
    assert.strictEqual(formatAuditDate('—'), '—');
    assert.strictEqual(formatAuditDate('undefined'), '—');
  });
});
