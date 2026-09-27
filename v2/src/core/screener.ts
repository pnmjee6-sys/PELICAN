/**
 * Client-Side Secret and Sensitivity Screener
 *
 * Implements defense-in-depth screening before text ever leaves the user's browser:
 * - Recognizable secrets (API keys, private keys, tokens, passwords) are flagged as 'secret' and skipped entirely.
 *   Handles both quoted and unquoted passwords and generic api keys.
 * - Sensitive topics (allergies, medical conditions, financial accounts, IDs) are flagged as 'sensitive'.
 * - Normal working text is classified as 'general'.
 */

export type PrivacyClassification = 'general' | 'sensitive' | 'secret';

export interface ScreeningResult {
  classification: PrivacyClassification;
  reason?: string;
  matchedPattern?: string;
}

// Secret patterns (API keys, credentials, tokens) - supports quoted and unquoted formats
const SECRET_PATTERNS: Array<{ name: string; pattern: RegExp }> = [
  { name: 'Private Key', pattern: /-----BEGIN[ A-Z0-9_-]*PRIVATE KEY-----/ },
  { name: 'Google API Key', pattern: /AIza[0-9A-Za-z-_]{35}/ },
  { name: 'OpenAI API Key', pattern: /sk-[a-zA-Z0-9_-]{20,}/ },
  { name: 'Anthropic API Key', pattern: /sk-ant-[a-zA-Z0-9_-]{20,}/ },
  { name: 'Supabase Secret Token', pattern: /sbp_[a-zA-Z0-9]{20,}/ },
  { name: 'GitHub Token', pattern: /(?:gh[pousr]_[A-Za-z0-9_]{36,}|github_pat_[0-9a-zA-Z_]{82})/ },
  { name: 'AWS Access Key', pattern: /AKIA[0-9A-Z]{16}/ },
  // Generic API key assignments: e.g. api_key=xyz, api-key: "xyz", access_token: xyz (quoted or unquoted)
  {
    name: 'Generic API Key Assignment',
    pattern: /(?:\bapi[_-]?key|\bsecret[_-]?key|\baccess[_-]?token|\bauth[_-]?token)\s*[:=]\s*['"]?([a-zA-Z0-9_\-\.]{12,})['"]?/i,
  },
  // Generic password assignments: e.g. password: xyz, passwd=xyz, pwd: 'xyz' (quoted or unquoted)
  {
    name: 'Generic Password Assignment',
    pattern: /(?:\bpassword|\bpasswd|\bpwd)\s*[:=]\s*['"]?([^\s'"]{6,})['"]?/i,
  },
  // Bearer Token
  { name: 'Bearer Token Assignment', pattern: /\bBearer\s+([a-zA-Z0-9_\-\.]{20,})/i },
  // Financial numbers
  { name: 'Credit Card Number', pattern: /\b(?:\d{4}[ -]?){3}\d{4}\b/ },
  { name: 'Social Security Number', pattern: /\b\d{3}-\d{2}-\d{4}\b/ },
];

// Sensitive patterns (allergies, medical, financials, PII, uncertain facts)
const SENSITIVE_PATTERNS: Array<{ name: string; pattern: RegExp }> = [
  {
    name: 'Allergy and Health Sensitivity',
    pattern: /\b(allergy|allergies|allergic|peanut|peanuts|epipen|anaphylaxis|gluten allergy|dairy allergy|nut allergy|shellfish allergy)\b/i,
  },
  {
    name: 'Medical Condition',
    pattern: /\b(medical condition|diagnosis|diagnosed with|prescription|medication|doctor's note|health record|bipolar|depression|cancer|hiv|pregnancy|addiction|diabetes|hospitalized)\b/i,
  },
  {
    name: 'Financial and Confidential',
    pattern: /\b(salary|income|bank account|routing number|credit score|passport number|social security|confidential project|strictly confidential|under nda|proprietary|tax return)\b/i,
  },
  {
    name: 'Identity and Contact Info',
    pattern: /\b(government id|national id|aadhaar|pan number|driver'?s license number|home address|street address|mailing address|phone number|mobile number|personal email|religion|religious belief|sexual orientation|gender identity|political affiliation|date of birth|birthday|legal name)\b/i,
  },
  { name: 'Email Address', pattern: /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/i },
  { name: 'Personal Address', pattern: /\b(?:my|our|home|live at|reside at)\b.{0,30}\b\d{1,6}\s+[A-Za-z0-9 .'-]{2,50}\b(?:street|st\.?|road|rd\.?|avenue|ave\.?|lane|ln\.?|drive|dr\.?|boulevard|blvd\.?)\b/i },
  { name: 'Phone Number', pattern: /\b(?:my|call me at|reach me at|phone(?: number)? is)\b.{0,25}(?:\+?\d[\d .()\-]{8,}\d)\b/i },
  { name: 'Religious Identity', pattern: /\b(?:i am|i'm|my faith is)\s+(?:a\s+)?(?:buddhist|christian|muslim|hindu|jewish|sikh|atheist)\b/i },
  {
    name: 'Uncertain / Speculative Fact',
    pattern: /\b(uncertain|not sure if|unconfirmed|tentative|provisional|speculative)\b/i,
  },
];

/**
 * Checks if the text contains recognizable secrets or credentials.
 */
export function containsSecret(text: string): boolean {
  if (!text) return false;
  return SECRET_PATTERNS.some((p) => p.pattern.test(text));
}

/**
 * Checks if the text contains sensitive or uncertain personal details.
 */
export function containsSensitive(text: string): boolean {
  if (!text) return false;
  return SENSITIVE_PATTERNS.some((p) => p.pattern.test(text));
}

/**
 * Classifies text into general, sensitive, or secret.
 */
export function screenText(text: string): ScreeningResult {
  if (!text || text.trim() === '') {
    return { classification: 'general' };
  }

  // 1. Check for recognizable secrets (skipped completely)
  for (const { name, pattern } of SECRET_PATTERNS) {
    if (pattern.test(text)) {
      return {
        classification: 'secret',
        reason: 'Recognizable credential or secret detected and skipped completely',
        matchedPattern: name,
      };
    }
  }

  // 2. Check for sensitive/uncertain personal details
  for (const { name, pattern } of SENSITIVE_PATTERNS) {
    if (pattern.test(text)) {
      return {
        classification: 'sensitive',
        reason: 'Contains sensitive or personal attribute requiring explicit approval',
        matchedPattern: name,
      };
    }
  }

  return { classification: 'general' };
}
