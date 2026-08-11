import { getCountries, getCountryCallingCode } from "libphonenumber-js";

/**
 * Full country list built dynamically from libphonenumber-js metadata,
 * enriched with English country names via the browser's Intl.DisplayNames API.
 * Pinned countries (US, GB, IN) are placed at the top; everything else is sorted alphabetically.
 */

const PINNED = ["US", "GB", "IN"];
const A_OFFSET = 127397; // 0x1F1E6 - 65 — regional indicator symbol offset

export function flagEmoji(code) {
  if (!code || code.length !== 2) return "";
  return code
    .toUpperCase()
    .split("")
    .map((c) => String.fromCodePoint(c.charCodeAt(0) + A_OFFSET))
    .join("");
}

let displayNames;
try {
  displayNames = new Intl.DisplayNames(["en"], { type: "region" });
} catch {
  displayNames = { of: (c) => c };
}

const ALL = getCountries()
  .map((code) => {
    let name;
    try {
      name = displayNames.of(code);
    } catch {
      name = code;
    }
    return {
      code,
      name: name || code,
      dial: `+${getCountryCallingCode(code)}`,
      flag: flagEmoji(code),
    };
  })
  .filter((c) => c.name && c.name.length > 1);

const pinnedInOrder = PINNED.map((code) => ALL.find((c) => c.code === code)).filter(
  Boolean
);
const rest = ALL.filter((c) => !PINNED.includes(c.code)).sort((a, b) =>
  a.name.localeCompare(b.name)
);

export const COUNTRIES = [...pinnedInOrder, ...rest];

export const DEFAULT_COUNTRY = COUNTRIES.find((c) => c.code === "US") || COUNTRIES[0];

export function findCountry(code) {
  return COUNTRIES.find((c) => c.code === code) || DEFAULT_COUNTRY;
}

/**
 * Approximate digit count expected for a valid national number.
 * libphonenumber-js's isValidPhoneNumber does the real work; this is used to
 * enable "Continue" as soon as the input matches an acceptable length.
 */
export const EXPECTED_LENGTHS = {
  US: [10],
  CA: [10],
  GB: [10, 11],
  IN: [10],
  AU: [9],
  DE: [10, 11],
  FR: [9],
  ES: [9],
  IT: [9, 10],
  BR: [10, 11],
  JP: [10],
  CN: [11],
  MX: [10],
  SG: [8],
  AE: [9],
  ZA: [9],
  NL: [9],
  SE: [7, 8, 9],
  NO: [8],
  DK: [8],
  IE: [9],
  NZ: [8, 9],
  KR: [9, 10],
};
