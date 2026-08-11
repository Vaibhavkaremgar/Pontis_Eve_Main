import React from "react";
import { AsYouType, isPossiblePhoneNumber } from "libphonenumber-js";
import { ChevronDown, Search, Check } from "lucide-react";

import { COUNTRIES, DEFAULT_COUNTRY, findCountry } from "../../data/countries";

export function useCountryPhone({ initialCountry = "US", initialDigits = "" } = {}) {
  const [country, setCountry] = React.useState(findCountry(initialCountry));
  const [digits, setDigits] = React.useState(initialDigits);

  const formatted = React.useMemo(() => {
    if (!digits) return "";
    const asYouType = new AsYouType(country.code);
    return asYouType.input(digits);
  }, [digits, country.code]);

  const isValid = React.useMemo(() => {
    if (!digits) return false;
    try {
      // "Possible" = matches the country's expected digit length.
      // We deliberately don't use isValidPhoneNumber here because it also checks
      // area-code assignment, which surprises users after they switch countries.
      return isPossiblePhoneNumber(digits, country.code);
    } catch {
      return false;
    }
  }, [digits, country.code]);

  return { country, setCountry, digits, setDigits, formatted, isValid };
}

export default function PhoneInput({
  country,
  setCountry,
  digits,
  setDigits,
  formatted,
  autoFocus = true,
}) {
  const [open, setOpen] = React.useState(false);
  const [query, setQuery] = React.useState("");
  const wrapRef = React.useRef(null);
  const searchRef = React.useRef(null);
  const listRef = React.useRef(null);

  React.useEffect(() => {
    if (open && searchRef.current) {
      searchRef.current.focus();
    }
  }, [open]);

  React.useEffect(() => {
    function onDoc(e) {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) {
        setOpen(false);
        setQuery("");
      }
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const filtered = React.useMemo(() => {
    if (!query.trim()) return COUNTRIES;
    const q = query.trim().toLowerCase();
    const starts = COUNTRIES.filter((c) => c.name.toLowerCase().startsWith(q));
    const contains = COUNTRIES.filter(
      (c) =>
        !c.name.toLowerCase().startsWith(q) &&
        (c.name.toLowerCase().includes(q) || c.dial.includes(q))
    );
    return [...starts, ...contains];
  }, [query]);

  const handleSelect = (c) => {
    setCountry(c);
    setOpen(false);
    setQuery("");
  };

  const handleDigitsChange = (e) => {
    // Strip everything non-numeric except a leading '+'
    const raw = e.target.value.replace(/[^\d]/g, "");
    setDigits(raw);
  };

  return (
    <div ref={wrapRef} className="relative">
      <div
        className={`flex items-stretch bg-white border rounded-xl transition-colors ${
          open
            ? "border-black/[0.24]"
            : "border-black/[0.08] focus-within:border-black/[0.24]"
        }`}
        data-testid="phone-input-wrapper"
      >
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          data-testid="country-dropdown-trigger"
          className="flex items-center gap-1.5 pl-3 pr-2 py-3.5 border-r border-black/[0.06] text-[14px] text-[#1F1F1F] hover:bg-black/[0.02] transition-colors rounded-l-xl"
        >
          <span className="text-[18px] leading-none" aria-hidden>
            {country.flag}
          </span>
          <span className="text-[13px] text-[#4A4A48] font-normal tabular-nums">
            {country.dial}
          </span>
          <ChevronDown
            className={`w-3.5 h-3.5 text-[#9A9A98] transition-transform ${
              open ? "rotate-180" : ""
            }`}
            strokeWidth={1.75}
          />
        </button>

        <input
          type="tel"
          inputMode="numeric"
          value={formatted}
          onChange={handleDigitsChange}
          onKeyDown={(e) => {
            // Block obvious non-digit keys except navigation / control
            const allowed = [
              "Backspace",
              "Delete",
              "Tab",
              "ArrowLeft",
              "ArrowRight",
              "Home",
              "End",
            ];
            if (
              !/[\d]/.test(e.key) &&
              !allowed.includes(e.key) &&
              !e.metaKey &&
              !e.ctrlKey
            ) {
              e.preventDefault();
            }
          }}
          placeholder="Phone number"
          autoFocus={autoFocus}
          data-testid="onboarding-phone-input"
          className="flex-1 bg-transparent border-none px-4 py-3.5 text-[16px] text-[#1F1F1F] placeholder:text-[#B5B5B3] focus:outline-none font-normal tabular-nums"
        />
      </div>

      {open && (
        <div
          data-testid="country-dropdown"
          className="absolute z-30 left-0 right-0 mt-1.5 bg-white border border-black/[0.08] rounded-xl shadow-[0_10px_40px_rgba(0,0,0,0.08)] overflow-hidden"
        >
          <div className="flex items-center gap-2 px-3 py-2.5 border-b border-black/[0.06]">
            <Search className="w-3.5 h-3.5 text-[#9A9A98]" strokeWidth={1.75} />
            <input
              ref={searchRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search country"
              data-testid="country-search-input"
              className="flex-1 bg-transparent border-none text-[13px] focus:outline-none placeholder:text-[#B5B5B3]"
            />
          </div>
          <div
            ref={listRef}
            className="max-h-[280px] overflow-y-auto eve-scroll py-1"
          >
            {filtered.length === 0 ? (
              <div className="px-4 py-6 text-center text-[12px] text-[#9A9A98]">
                No countries match "{query}"
              </div>
            ) : (
              filtered.map((c) => (
                <button
                  key={c.code}
                  onClick={() => handleSelect(c)}
                  data-testid={`country-option-${c.code}`}
                  className={`w-full flex items-center gap-2.5 px-3 py-2 text-left transition-colors ${
                    c.code === country.code
                      ? "bg-black/[0.03]"
                      : "hover:bg-black/[0.03]"
                  }`}
                >
                  <span className="text-[17px] leading-none shrink-0" aria-hidden>
                    {c.flag}
                  </span>
                  <span className="flex-1 text-[13px] text-[#1F1F1F] truncate">
                    {c.name}
                  </span>
                  <span className="text-[12px] text-[#9A9A98] tabular-nums">
                    {c.dial}
                  </span>
                  {c.code === country.code && (
                    <Check className="w-3.5 h-3.5 text-[#1F1F1F]" strokeWidth={2} />
                  )}
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}

PhoneInput.DEFAULT_COUNTRY = DEFAULT_COUNTRY;
