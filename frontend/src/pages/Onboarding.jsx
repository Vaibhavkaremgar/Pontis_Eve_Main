import React from "react";
import axios from "axios";
import { useNavigate, useSearchParams } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import { toast, Toaster } from "sonner";
import {
  FileText,
  Award,
  Check,
  Upload as UploadIcon,
  Plus,
  X,
  ArrowRight,
  ArrowLeft,
} from "lucide-react";

import { MOCK_USER_PROFILE } from "../mock";
import PhoneInput, { useCountryPhone } from "../components/onboarding/PhoneInput";
import VoiceIntake from "../components/onboarding/VoiceIntake";
import {
  loadOnboardingState,
  saveOnboardingState,
  resumableStep,
  isVoiceIntakeCompleteStatus,
} from "../lib/onboardingStorage";
import { mergeProfilesForDisplay, normalizeProfileForDisplay } from "../lib/profileNormalization";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

const TYPEWRITER_LINES = [
  "Your resume is just the baseline.",
  "Now take a relaxed 3-5 minutes to tell Eve what you actually want next,",
  "and she'll build your Living Profile in the background,",
  "and match you with the right jobs and companies.",
];

const FALLBACK_SUMMARY = [
  {
    label: "Target roles",
    value: "Business Development, Growth Manager, AI Automation Specialist",
  },
  {
    label: "Compensation expectations",
    value: "₹15–30L in India · $120–160k CAD in Canada",
  },
  { label: "Locations", value: "Hyderabad and Canada — open to remote" },
  {
    label: "Standout strengths",
    value:
      "B2B SaaS sales, CRM strategy, revenue growth, ex-founder in AI automation",
  },
  { label: "Notable win", value: "Scaled ARR from $200k → $1M at ABC Growth" },
  {
    label: "Availability",
    value: "Actively looking · ready to start within 4 weeks",
  },
];

/* ---------- Layout primitives ---------- */

function ProgressDots({ step, total = 5 }) {
  return (
    <div className="flex items-center gap-1.5" data-testid="onboarding-progress-dots">
      {Array.from({ length: total }, (_, i) => i + 1).map((i) => (
        <motion.div
          key={i}
          layout
          className={`h-1 rounded-full transition-colors ${
            i === step
              ? "bg-[#1F1F1F] w-8"
              : i < step
              ? "bg-[#1F1F1F]/45 w-7"
              : "bg-black/[0.08] w-7"
          }`}
        />
      ))}
    </div>
  );
}

function PrimaryButton({ children, onClick, disabled, testId }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      data-testid={testId}
      className="w-full bg-[#1F1F1F] text-white text-[14px] font-medium rounded-full py-3.5 hover:bg-black transition-all disabled:bg-black/[0.08] disabled:text-[#B5B5B3] disabled:cursor-not-allowed flex items-center justify-center gap-2"
    >
      {children}
    </button>
  );
}

const stepMotion = {
  initial: { opacity: 0, y: 14 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -14 },
  transition: { duration: 0.42, ease: [0.4, 0, 0.2, 1] },
};

function Shell({ step, children, actions, hideActions = false, backButton }) {
  return (
    <div
      className="min-h-screen bg-[#FBFBF9] text-[#1F1F1F] flex flex-col"
      data-testid={`onboarding-step-${step}`}
    >
      <header className="w-full pt-8 pb-2 flex justify-center relative">
        {backButton && (
          <div className="absolute left-6 top-1/2 -translate-y-1/2">
            {backButton}
          </div>
        )}
        <ProgressDots step={step} />
      </header>

      <main className="flex-1 flex flex-col items-center justify-center px-6">
        <div className="w-full max-w-md">{children}</div>
      </main>

      {!hideActions && (
        <footer className="w-full pb-10 pt-6 flex justify-center px-6">
          <AnimatePresence mode="wait">
            <motion.div
              key={step}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.3, ease: "easeOut" }}
              className="w-full max-w-md"
            >
              {actions}
            </motion.div>
          </AnimatePresence>
        </footer>
      )}
    </div>
  );
}

/* ---------- Step 1: Phone with country dropdown ---------- */

function StepPhone({ phone }) {
  return (
    <div>
      <h1 className="text-[26px] font-medium tracking-tight leading-tight mb-8">
        What's your phone number?
      </h1>
      <PhoneInput
        country={phone.country}
        setCountry={phone.setCountry}
        digits={phone.digits}
        setDigits={phone.setDigits}
        formatted={phone.formatted}
      />
      <p className="text-[12.5px] text-[#9A9A98] mt-3 leading-relaxed">
        Where Eve sends your interview requests. (Kept completely private.)
      </p>
    </div>
  );
}

/* ---------- Step 2: Uploads (red asterisk, multi certs) ---------- */

function ResumeRow({ file, onSelect, onClear }) {
  const inputRef = React.useRef(null);
  return (
    <div className="flex items-center justify-between bg-white border border-black/[0.06] rounded-xl px-4 py-3.5">
      <div className="flex items-center gap-3 min-w-0">
        <span className="w-9 h-9 rounded-lg bg-black/[0.03] flex items-center justify-center shrink-0">
          <FileText className="w-[18px] h-[18px] text-[#4A4A48]" strokeWidth={1.5} />
        </span>
        <div className="min-w-0">
          <div className="flex items-center gap-1">
            <span className="text-[13.5px] font-medium text-[#1F1F1F]">
              Resume / CV
            </span>
            <span
              className="text-[#E11D48] font-medium"
              aria-label="required"
              data-testid="resume-required-asterisk"
            >
              *
            </span>
          </div>
          {file ? (
            <p
              className="text-[12px] text-[#4A4A48] truncate mt-0.5 flex items-center gap-1 max-w-[240px]"
              data-testid="onboarding-resume-filename"
            >
              <Check className="w-3 h-3 text-[#2E7538]" strokeWidth={2.25} />
              {file.name}
            </p>
          ) : (
            <p className="text-[11.5px] text-[#9A9A98] mt-0.5">
              PDF · text-based files parse best
            </p>
          )}
        </div>
      </div>

      <input
        ref={inputRef}
        type="file"
        accept=".pdf"
        className="hidden"
        onChange={(e) => e.target.files?.[0] && onSelect(e.target.files[0])}
        data-testid="onboarding-resume-input"
      />

      {file ? (
        <div className="flex items-center gap-1.5">
          <button
            onClick={() => inputRef.current?.click()}
            className="text-[12px] font-medium px-3 py-1.5 rounded-full bg-black/[0.04] hover:bg-black/[0.07] text-[#1F1F1F] transition-colors"
            data-testid="onboarding-resume-replace"
          >
            Replace
          </button>
          <button
            onClick={onClear}
            className="w-7 h-7 rounded-full hover:bg-black/[0.05] flex items-center justify-center text-[#9A9A98] hover:text-[#1F1F1F] transition-colors"
            aria-label="Remove resume"
            data-testid="onboarding-resume-clear"
          >
            <X className="w-3.5 h-3.5" strokeWidth={1.75} />
          </button>
        </div>
      ) : (
        <button
          onClick={() => inputRef.current?.click()}
          className="text-[12px] font-medium px-3.5 py-1.5 rounded-full bg-[#1F1F1F] text-white hover:bg-black transition-colors inline-flex items-center gap-1.5"
          data-testid="onboarding-resume-upload"
        >
          <UploadIcon className="w-3 h-3" strokeWidth={2} />
          Upload
        </button>
      )}
    </div>
  );
}

function CertificationsRow({ files, onAdd, onRemove }) {
  const inputRef = React.useRef(null);
  return (
    <div className="bg-white border border-black/[0.06] rounded-xl px-4 py-3.5 space-y-2.5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3 min-w-0">
          <span className="w-9 h-9 rounded-lg bg-black/[0.03] flex items-center justify-center shrink-0">
            <Award className="w-[18px] h-[18px] text-[#4A4A48]" strokeWidth={1.5} />
          </span>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-[13.5px] font-medium text-[#1F1F1F]">
                Certifications
              </span>
              <span className="text-[10.5px] uppercase tracking-wide text-[#9A9A98]">
                Optional
              </span>
            </div>
            <p className="text-[11.5px] text-[#9A9A98] mt-0.5">
              Add as many as you'd like
            </p>
          </div>
        </div>

        <input
          ref={inputRef}
          type="file"
          multiple
          accept=".pdf,.png,.jpg,.jpeg"
          className="hidden"
          onChange={(e) => {
            const list = Array.from(e.target.files || []);
            if (list.length) onAdd(list);
            e.target.value = "";
          }}
          data-testid="onboarding-certs-input"
        />

        <div className="flex items-center gap-1.5">
          <button
            onClick={() => inputRef.current?.click()}
            className="text-[12px] font-medium px-3 py-1.5 rounded-full bg-white border border-black/[0.1] hover:bg-black/[0.03] text-[#1F1F1F] transition-colors inline-flex items-center gap-1"
            data-testid="onboarding-certs-add"
          >
            <Plus className="w-3 h-3" strokeWidth={2.25} />
            Add
          </button>
          <button
            onClick={() => inputRef.current?.click()}
            className="text-[12px] font-medium px-3.5 py-1.5 rounded-full bg-[#1F1F1F] text-white hover:bg-black transition-colors inline-flex items-center gap-1.5"
            data-testid="onboarding-certs-upload"
          >
            <UploadIcon className="w-3 h-3" strokeWidth={2} />
            Upload
          </button>
        </div>
      </div>

      {files.length > 0 && (
        <ul className="space-y-1.5 pt-1" data-testid="onboarding-certs-list">
          {files.map((f, i) => (
            <li
              key={`${f.name}-${i}`}
              className="flex items-center justify-between bg-black/[0.02] rounded-lg px-3 py-1.5"
            >
              <span className="text-[12px] text-[#4A4A48] truncate flex items-center gap-1.5 min-w-0">
                <Check className="w-3 h-3 text-[#2E7538] shrink-0" strokeWidth={2.25} />
                <span className="truncate">{f.name}</span>
              </span>
              <button
                onClick={() => onRemove(i)}
                className="w-6 h-6 rounded-full hover:bg-black/[0.05] flex items-center justify-center text-[#9A9A98] hover:text-[#1F1F1F] transition-colors ml-2 shrink-0"
                aria-label={`Remove ${f.name}`}
              >
                <X className="w-3 h-3" strokeWidth={1.75} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ---------- Verification helpers ---------- */

export function normalizeEmail(raw) {
  return String(raw || "").trim().toLowerCase();
}

export function normalizePhone(raw) {
  return String(raw || "").replace(/\D/g, "");
}

/**
 * Returns an array of error message strings (empty = both match / no data to compare).
 * loginEmail  — email from the OAuth provider profile
 * enteredPhone — digits the candidate typed (any format; we strip non-digits)
 * resumeProfile — the parsed profile object returned by the backend
 */
export function checkVerificationErrors(loginEmail, enteredPhone, resumeProfile) {
  const errors = [];
  const resumeEmail = normalizeEmail(resumeProfile?.email);
  const loginNorm = normalizeEmail(loginEmail);
  if (loginNorm && resumeEmail && loginNorm !== resumeEmail) {
    errors.push("email");
  }
  const resumePhone = normalizePhone(resumeProfile?.phone);
  const enteredNorm = normalizePhone(enteredPhone);
  // Only compare the last N digits of the longer number to handle country-code differences
  if (enteredNorm && resumePhone) {
    const len = Math.min(enteredNorm.length, resumePhone.length, 10);
    if (enteredNorm.slice(-len) !== resumePhone.slice(-len)) {
      errors.push("phone");
    }
  }
  return errors;
}

function VerificationErrors({ errors }) {
  if (!errors || errors.length === 0) return null;
  const emailMismatch = errors.includes("email");
  const phoneMismatch = errors.includes("phone");
  return (
    <div className="mt-4 space-y-2" data-testid="verification-errors">
      {emailMismatch && (
        <p
          className="text-[12.5px] text-[#E11D48] leading-relaxed"
          data-testid="verification-error-email"
        >
          The login email and email mentioned in your resume do not match. Please correct your details.
        </p>
      )}
      {phoneMismatch && (
        <p
          className="text-[12.5px] text-[#E11D48] leading-relaxed"
          data-testid="verification-error-phone"
        >
          The mobile number you entered and the mobile number mentioned in your resume do not match. Please correct your details.
        </p>
      )}
    </div>
  );
}

function StepUpload({
  resumeFile,
  setResumeFile,
  certsFiles,
  setCertsFiles,
  verificationErrors,
}) {
  return (
    <div>
      <h1 className="text-[26px] font-medium tracking-tight leading-tight mb-2">
        Let's build your profile.
      </h1>
      <p className="text-[13.5px] text-[#9A9A98] mb-8 leading-relaxed">
        Drop in a resume so Eve can extract your experience. Add certifications
        if you'd like them showcased too.
      </p>

      <div className="space-y-3">
        <ResumeRow
          file={resumeFile}
          onSelect={setResumeFile}
          onClear={() => setResumeFile(null)}
        />
        <CertificationsRow
          files={certsFiles}
          onAdd={(list) => setCertsFiles((prev) => [...prev, ...list])}
          onRemove={(idx) =>
            setCertsFiles((prev) => prev.filter((_, i) => i !== idx))
          }
        />
      </div>
      <VerificationErrors errors={verificationErrors} />
    </div>
  );
}

/* ---------- Step 3: Parsing buffer with typewriter ---------- */

function useTypewriter(fullText, speed = 28) {
  const [count, setCount] = React.useState(0);
  const [done, setDone] = React.useState(false);

  React.useEffect(() => {
    setCount(0);
    setDone(false);
    let i = 0;
    const id = setInterval(() => {
      i += 1;
      setCount(i);
      if (i >= fullText.length) {
        clearInterval(id);
        setDone(true);
      }
    }, speed);
    return () => clearInterval(id);
  }, [fullText, speed]);

  return { text: fullText.slice(0, count), done };
}

function StepParsing({ onComplete, parsingReady, parsingError }) {
  const fullText = TYPEWRITER_LINES.join("\n");
  const { text, done: typewriterDone } = useTypewriter(fullText, 28);

  React.useEffect(() => {
    if (typewriterDone && parsingReady) {
      const t = setTimeout(onComplete, 900);
      return () => clearTimeout(t);
    }
  }, [typewriterDone, parsingReady, onComplete]);

  const showingLines = text.split("\n");

  return (
    <div className="flex flex-col items-center gap-8 text-center">
      <div className="relative w-11 h-11">
        <div className="absolute inset-0 rounded-full border-2 border-black/[0.06]" />
        <div className="absolute inset-0 rounded-full border-2 border-t-[#1F1F1F] border-r-transparent border-b-transparent border-l-transparent animate-spin" />
      </div>

      <div
        className="space-y-2"
        data-testid="onboarding-parsing-typewriter"
      >
        {TYPEWRITER_LINES.map((line, idx) => {
          const rendered = showingLines[idx] || "";
          const isCurrent =
            idx === showingLines.length - 1 && !typewriterDone;
          return (
            <p
              key={idx}
              className="text-[16.5px] leading-[1.5] text-[#1F1F1F] font-normal tracking-tight max-w-lg mx-auto min-h-[1.6em]"
            >
              {rendered}
              {isCurrent && (
                <span
                  className="inline-block w-[2px] h-[1em] bg-[#1F1F1F] ml-[1px] align-middle animate-pulse"
                  aria-hidden
                />
              )}
            </p>
          );
        })}
      </div>

      {parsingError && (
        <p
          className="text-[12px] text-[#E11D48] max-w-xs"
          data-testid="parsing-error"
        >
          {parsingError}
        </p>
      )}
    </div>
  );
}

/* ---------- Step 4: Voice intake — handled by VoiceIntake component ---------- */

/* ---------- Step 5: Bridge ---------- */

export function buildCareerSummary(profile) {
  if (!profile) return "";
  const merged = normalizeProfileForDisplay(profile);
  const role = (merged.headline || merged.current_role || "").trim();
  const skills = (merged.keySkills || []).slice(0, 5);
  const targetRoles = (merged.preferred_roles || []).slice(0, 3);
  const rawData = (merged.raw_data && typeof merged.raw_data === "object") ? merged.raw_data : {};
  const rolePrefBio = (rawData.role_preference_bio || "").trim();
  const additionalInfo = (merged.additional_information || "").trim();

  const sentences = [];

  // Sentence 1: current role + skills background
  if (role && skills.length) {
    sentences.push(`${role} with a background in ${skills.slice(0, 4).join(", ")}.`);
  } else if (role) {
    sentences.push(`${role} with cross-functional experience.`);
  } else if (skills.length) {
    sentences.push(`Professional with a background in ${skills.slice(0, 4).join(", ")}.`);
  }

  // Sentence 2: voice-derived career interest / role preference bio
  if (rolePrefBio && rolePrefBio.length > 10) {
    const clean = rolePrefBio.replace(/\.$/, "");
    sentences.push(`${clean}.`);
  } else if (additionalInfo && additionalInfo.length > 10) {
    const firstSentence = additionalInfo.split(/[.!?]/)[0].trim();
    if (firstSentence) sentences.push(`${firstSentence}.`);
  }

  // Sentence 3: target roles
  if (targetRoles.length) {
    sentences.push(`Currently targeting ${targetRoles.join(", ")} roles.`);
  } else if (role) {
    sentences.push(`Open to new opportunities that leverage their expertise.`);
  }

  // Sentence 4: skills reinforcement (only if we have room and haven't already covered them)
  if (sentences.length < 4 && skills.length > 2) {
    sentences.push(`Brings hands-on experience with ${skills.slice(0, 5).join(", ")}.`);
  }

  return sentences.slice(0, 4).join(" ");
}

export function buildSummary(profile) {
  const merged = profile ? normalizeProfileForDisplay(profile) : null;
  if (!merged) return FALLBACK_SUMMARY;
  const items = [];

  // Replace "Positioning" with a 3-4 line professional summary paragraph
  const summaryText = buildCareerSummary(profile);
  if (summaryText) items.push({ label: "Summary", value: summaryText });

  if (merged.location) items.push({ label: "Location", value: merged.location });
  if (merged.keySkills?.length)
    items.push({ label: "Top skills", value: merged.keySkills.slice(0, 8).join(", ") });
  if (merged.experience?.length) {
    const first = merged.experience[0];
    items.push({
      label: "Latest role",
      value: (first.title || "") + (first.company ? " at " + first.company : "") + (first.dates ? " · " + first.dates : ""),
    });
  }
  if (merged.certifications?.length)
    items.push({ label: "Certifications", value: merged.certifications.slice(0, 8).join(", ") });
  if (merged.education?.length) {
    const edu = merged.education[0];
    items.push({ label: "Education", value: (edu.degree || "") + (edu.institution ? " · " + edu.institution : "") });
  }
  if (merged.preferred_roles?.length)
    items.push({ label: "Target roles", value: merged.preferred_roles.slice(0, 6).join(", ") });
  if (merged.additional_information)
    items.push({ label: "Career context", value: merged.additional_information });
  return items.length ? items : FALLBACK_SUMMARY;
}

function StepBridge({ profile, voiceIntakeCompleted }) {
  const items = buildSummary(profile);
  return (
    <div>
      <h1 className="text-[26px] font-medium tracking-tight leading-tight mb-2">
        Here's what Eve heard.
      </h1>
      <p className="text-[13.5px] text-[#9A9A98] mb-4 leading-relaxed">
        A quick recap before you head into your dashboard. You can edit any of
        this later.
      </p>

      {voiceIntakeCompleted && (
        <div
          className="flex items-center gap-2 bg-[#E7F2E4] rounded-xl px-4 py-3 mb-6"
          data-testid="voice-intake-complete-banner"
        >
          <span className="w-2 h-2 rounded-full bg-[#2E7538] shrink-0" />
          <p className="text-[12.5px] text-[#2E7538] font-medium">
            Voice intake complete — your profile has been updated.
          </p>
        </div>
      )}

      <ul className="space-y-5" data-testid="onboarding-summary-list">
        {items.map((item) => (
          <li key={item.label} className="flex items-start gap-3">
            <span className="mt-[9px] w-1 h-1 rounded-full bg-[#1F1F1F] shrink-0" />
            <div className="min-w-0">
              <p className="text-[11px] uppercase tracking-wide text-[#9A9A98] font-normal">
                {item.label}
              </p>
              <p className="text-[13.5px] text-[#1F1F1F] mt-0.5 leading-relaxed font-normal">
                {item.value}
              </p>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ---------- Main container ---------- */

export default function Onboarding() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();  

  // Accept backend redirect: /onboarding?linkedin_profile=<encoded>
  // Process the OAuth param synchronously so storage is ready before any effect runs.
  const linkedInProfileParam = searchParams.get("linkedin_profile");
  const candidateIdParam = searchParams.get("candidate_id");
  const candidateTokenParam = searchParams.get("candidate_token");
  const resumeVoiceParam = searchParams.get("resume_voice");
  const isOAuthCallback = !!linkedInProfileParam;

  // useMemo runs synchronously during render — save to storage before effects fire.
  const persisted = React.useMemo(() => {
    if (linkedInProfileParam) {
      try {
        const profile = JSON.parse(decodeURIComponent(linkedInProfileParam));
        const current = loadOnboardingState();
        const next = {
          ...current,
          linkedInAuthenticated: true,
          linkedInProfile: profile,
          candidateId: candidateIdParam || current.candidateId || null,
          candidateToken: candidateTokenParam || current.candidateToken || null,
        };
        saveOnboardingState(next);
        console.log("[LinkedIn] current URL:", window.location.href);
        console.log("[LinkedIn] linkedin_profile exists:", true);
        console.log("[LinkedIn] persisted state:", next);
        return next;
      } catch (_) {}
    }
    return loadOnboardingState();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const [step, setStep] = React.useState(() => {
    const base = resumableStep(persisted);
    // If coming from dashboard "Chat with Eve" with incomplete voice intake, jump to step 4
    if (resumeVoiceParam && persisted.parsedProfile && !persisted.voiceIntakeCompleted) return 4;
    return base;
  });

  // Guard: must have gone through LinkedIn auth (either via storage or URL param)
  React.useEffect(() => {
    const state = loadOnboardingState();
    if (!state.linkedInAuthenticated) {
      navigate("/", { replace: true });
      return;
    }
    // Clean the URL after saving the param
    if (isOAuthCallback || resumeVoiceParam) {
      console.log("[LinkedIn] redirecting to: /onboarding");
      navigate("/onboarding", { replace: true });
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Welcome-back toast when the user actually returns mid-flow.
  React.useEffect(() => {
    const initialStep = resumableStep(persisted);
    const hasProgress =
      !!persisted.phoneDigits ||
      !!persisted.parsedProfile ||
      !!persisted.resumeMeta;
    if (initialStep >= 2 && hasProgress) {
      const t = setTimeout(() => {
        toast.custom(
          () => (
            <div
              data-testid="welcome-back-toast"
              className="flex items-center gap-2.5 bg-white border border-[#2DD4BF]/45 rounded-full pl-3 pr-4 py-2 shadow-[0_6px_24px_rgba(20,184,166,0.18)]"
            >
              <span className="w-1.5 h-1.5 rounded-full bg-[#2DD4BF] shrink-0" />
              <span className="text-[12.5px] text-[#1F1F1F] font-normal">
                Welcome back — picking up on Step {initialStep}
              </span>
            </div>
          ),
          { duration: 4500 }
        );
      }, 450);
      return () => clearTimeout(t);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const phone = useCountryPhone({
    initialCountry: persisted.countryCode || "US",
    initialDigits: persisted.phoneDigits || "",
  });

  const [resumeFile, setResumeFile] = React.useState(null); // real File, can't persist
  const [certsFiles, setCertsFiles] = React.useState([]);
  const [parsedProfile, setParsedProfile] = React.useState(persisted.parsedProfile);
  const [candidateId, setCandidateId] = React.useState(persisted.candidateId ?? null);
  const [parsingReady, setParsingReady] = React.useState(!!persisted.parsedProfile);
  const [parsingError, setParsingError] = React.useState(null);
  const [verificationErrors, setVerificationErrors] = React.useState([]);

  const [transcription, setTranscription] = React.useState(!!persisted.transcription);
  const [muted, setMuted] = React.useState(!!persisted.muted);
  const [voiceElapsedMs, setVoiceElapsedMs] = React.useState(
    persisted.voiceElapsedMs || 0
  );
  const [voiceIntakeCompleted, setVoiceIntakeCompleted] = React.useState(
    !!persisted.voiceIntakeCompleted
  );

  // Track how long the candidate stays on the voice intake screen (Step 4).
  const voiceStartRef = React.useRef(null);
  React.useEffect(() => {
    if (step === 4) {
      voiceStartRef.current = Date.now();
    } else {
      voiceStartRef.current = null;
    }
  }, [step]);

  const finishVoiceIntake = React.useCallback((intakeResult) => {
    const startedAt = voiceStartRef.current;
    if (startedAt) {
      const elapsed = Date.now() - startedAt;
      setVoiceElapsedMs(elapsed);
    }
    const completed = isVoiceIntakeCompleteStatus(intakeResult?.status);
    setVoiceIntakeCompleted(completed);
    const mergedProfile = mergeProfilesForDisplay(
      parsedProfile || {},
      intakeResult?.profile || intakeResult?.profile_updates || {}
    );
    setParsedProfile(mergedProfile);

    const s = loadOnboardingState();
    saveOnboardingState({
      ...s,
      parsedProfile: mergedProfile,
      voiceIntakeCompleted: completed,
    });

    setStep(5);
  }, [parsedProfile]);

  // Persist state whenever the shape changes
  React.useEffect(() => {
    const current = loadOnboardingState();
    saveOnboardingState({
      // Preserve auth fields — never let the persist effect wipe them
      linkedInAuthenticated: current.linkedInAuthenticated,
      linkedInProfile: current.linkedInProfile,
      candidateToken: current.candidateToken,
      isOpenToMatches: current.isOpenToMatches,
      step,
      countryCode: phone.country.code,
      phoneDigits: phone.digits,
      resumeMeta: resumeFile
        ? { name: resumeFile.name, size: resumeFile.size }
        : persisted.resumeMeta,
      certsMeta: certsFiles.length
        ? certsFiles.map((f) => ({ name: f.name, size: f.size }))
        : persisted.certsMeta,
      parsedProfile,
      candidateId,
      candidateToken: current.candidateToken ?? null,
      transcription,
      muted,
      voiceElapsedMs,
      voiceIntakeCompleted,
    });
  }, [
    step,
    phone.country.code,
    phone.digits,
    resumeFile,
    certsFiles,
    parsedProfile,
    candidateId,
    transcription,
    muted,
    voiceElapsedMs,
    voiceIntakeCompleted,
    persisted.resumeMeta,
    persisted.certsMeta,
  ]);

  const firstName = (parsedProfile?.name || MOCK_USER_PROFILE.name || "there")
    .split(" ")[0];

  const kickOffParsing = React.useCallback(async () => {
    if (!resumeFile) return;
    setParsingReady(false);
    setParsingError(null);
    try {
      const fd = new FormData();
      fd.append("file", resumeFile);
      // If we already have a candidateId (e.g. test candidate re-onboarding),
      // pass it so the backend updates the existing record instead of creating a duplicate.
      const existingId = candidateId || loadOnboardingState().candidateId;
      const url = existingId
        ? `${API}/onboarding/parse-resume?existing_id=${existingId}`
        : `${API}/onboarding/parse-resume`;
      const res = await axios.post(url, fd, {      
        headers: { "Content-Type": "multipart/form-data" },
      });
      const { candidate_id, candidate_token, ...profile } = res.data;
      console.log("Resume parsing candidate_id:", candidate_id);
      // Immediately persist the new candidate_id to storage before any state update
      // so Dashboard always reads the correct ID even if it mounts before the effect runs
      if (candidate_id) {
        const current = loadOnboardingState();
        saveOnboardingState({ ...current, candidateId: candidate_id, candidateToken: candidate_token || current.candidateToken || null });
      }
      setParsedProfile(profile);
      if (candidate_id) {
        setCandidateId(candidate_id);
        if (candidate_token) {
          const current = loadOnboardingState();
          saveOnboardingState({ ...current, candidateId: candidate_id, candidateToken: candidate_token });
        }
        // Upload any pending certs now that we have a candidate_id
        if (certsFiles.length > 0) {
          certsFiles.forEach((certFile) => {
            const fd2 = new FormData();
            fd2.append("file", certFile);
            axios.post(`${API}/candidate/${candidate_id}/certificates/upload`, fd2).catch(() => {});
          });
        }
      }
      // Verify email + phone against parsed resume before advancing
      const loginEmail = loadOnboardingState().linkedInProfile?.email;
      const vErrors = checkVerificationErrors(loginEmail, phone.formatted, profile);
      setVerificationErrors(vErrors);
      if (vErrors.length > 0) {
        // Stay on step 2 — do not advance to parsing screen
        setParsingReady(false);
        return false;
      }
        setParsingReady(true);
    } catch (err) {
      console.error("resume parse failed", err);
      const detail =
        err?.response?.data?.detail ||
        "Couldn't read that PDF. You can retry, or continue with defaults.";
      setParsingError(detail);
      setTimeout(() => setParsingReady(true), 2000);
      toast.error("Resume parse failed — using defaults");
    }
  }, [resumeFile, certsFiles, candidateId, phone.formatted]);

  const handleContinueUpload = async () => {
    setVerificationErrors([]);
    setStep(3);
    const passed = await kickOffParsing();
    if (passed === false) {
      setStep(2);
    }
  };

  const actions = (() => {
    if (step === 1) {
      return (
        <PrimaryButton
          disabled={!phone.isValid}
          onClick={() => setStep(2)}
          testId="onboarding-continue-phone"
        >
          Continue
        </PrimaryButton>
      );
    }
    if (step === 2) {
      return (
        <PrimaryButton
          disabled={!resumeFile}
          onClick={handleContinueUpload}
          testId="onboarding-continue-upload"
        >
          Continue
        </PrimaryButton>
      );
    }
    if (step === 4) {
      return null; // VoiceIntake manages its own end-call button
    }
    if (step === 5) {
      return (
        <PrimaryButton
          onClick={() => {
            const s = loadOnboardingState();
            saveOnboardingState({ ...s, newlyOnboarded: true });
            navigate("/dashboard");
          }}
          testId="onboarding-enter-dashboard"
        >
          Enter Dashboard
          <ArrowRight className="w-4 h-4" strokeWidth={2} />
        </PrimaryButton>
      );
    }
    return null;
  })();

  const uploadBackButton = step === 2 ? (
    <button
      onClick={() => setStep(1)}
      data-testid="onboarding-upload-back"
      className="flex items-center gap-1 text-[13px] font-bold text-[#1F1F1F] hover:opacity-70 transition-opacity"
    >
      <ArrowLeft className="w-4 h-4" strokeWidth={2.5} />
      Back
    </button>
  ) : null;

  return (
    <Shell step={step} actions={actions} hideActions={step === 3 || step === 4} backButton={uploadBackButton}>
      <Toaster position="top-right" richColors closeButton />
      <AnimatePresence mode="wait">
        <motion.div key={step} {...stepMotion}>
          {step === 1 && <StepPhone phone={phone} />}
          {step === 2 && (
            <StepUpload
              resumeFile={resumeFile}
              setResumeFile={setResumeFile}
              certsFiles={certsFiles}
              setCertsFiles={setCertsFiles}
              verificationErrors={verificationErrors}
            />
          )}
          {step === 3 && (
            <StepParsing
              onComplete={() => setStep(4)}
              parsingReady={parsingReady}
              parsingError={parsingError}
            />
          )}
          {step === 4 && (
            <VoiceIntake
              firstName={firstName}
              candidateId={candidateId}
              candidateProfile={parsedProfile}
              onComplete={finishVoiceIntake}
            />
          )}
          {step === 5 && <StepBridge profile={parsedProfile} voiceIntakeCompleted={voiceIntakeCompleted} />}
        </motion.div>
      </AnimatePresence>
    </Shell>
  );
}
