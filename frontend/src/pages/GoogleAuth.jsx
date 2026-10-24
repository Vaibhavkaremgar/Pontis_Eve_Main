import React from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { motion } from "framer-motion";
import { loadOnboardingState, saveOnboardingState, clearOnboardingState } from "../lib/onboardingStorage";

const BACKEND_URL = import.meta.env.VITE_BACKEND_URL;

export default function GoogleAuth() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState(null);

  React.useEffect(() => {
    const exchangeCode = searchParams.get("exchange_code");
    const authError = searchParams.get("auth_error");
    if (authError) {
      setError(authError === "linkedin_cancelled" ? "LinkedIn sign-in was cancelled." : "Sign-in could not be completed. Please try again.");
      window.history.replaceState({}, "", "/");
      return;
    }
    if (exchangeCode) {
      fetch(`${BACKEND_URL}/api/auth/session/exchange`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ exchange_code: exchangeCode }),
      }).then((response) => {
        if (!response.ok) throw new Error("exchange_failed");
        return response.json();
      }).then((session) => {
        if (session.google_registration_blocked) {
          clearOnboardingState();
          throw new Error("google_registration_blocked");
        }
        const state = { ...loadOnboardingState(), authenticated: true,
          candidateId: session.candidate_id || null, candidateToken: session.candidate_token || null,
          googleRegistrationToken: session.google_registration_token || null,
          profile: session.profile || session.linkedin_profile || null };
        if (session.needs_onboarding || !session.candidate_id) {
          clearOnboardingState(); saveOnboardingState(state); navigate("/onboarding", { replace: true });
        } else { saveOnboardingState(state); navigate("/dashboard", { replace: true }); }
      }).catch((requestError) => setError(
        requestError?.message === "google_registration_blocked"
            ? "Google did not provide a verified email. Use a verified Google account or contact support."
            : "Sign-in could not be completed. Please try again."
      ));
      return;
    }
    const stored = loadOnboardingState();
    if (stored.authenticated && stored.candidateId) navigate("/dashboard", { replace: true });
    else if (stored.authenticated) navigate("/onboarding", { replace: true });
  }, [navigate, searchParams]);

  const handleGoogle = () => { setLoading(true); setError(null); window.location.assign(`${BACKEND_URL}/api/auth/google/init?redirect=true`); };
  const handleLinkedIn = () => { setLoading(true); setError(null); window.location.assign(`${BACKEND_URL}/api/auth/linkedin/login`); };

  return (
    <div className="min-h-screen bg-[#FBFBF9] text-[#242424] flex flex-col items-center justify-center px-6">
      <motion.div initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} className="w-full max-w-md">
        <div className="mb-10 text-center">
          <span className="text-[28px] font-medium tracking-tight text-[#171717]">Eve</span>
          <p className="text-[13.5px] text-[#5F5F5F] mt-1.5">Your AI-powered career partner.</p>
        </div>
        <div className="bg-white border border-black/[0.08] rounded-2xl px-6 py-8 shadow-[0_8px_30px_rgba(0,0,0,0.06)]">
          <h1 className="text-[20px] font-medium text-[#171717] mb-1.5">Get started</h1>
          <p className="text-[13px] text-[#5F5F5F] mb-7 leading-relaxed">Sign in to build your Living Profile and get matched with the right roles.</p>
          <button onClick={handleGoogle} disabled={loading} className="w-full bg-white border border-black/[0.14] text-[#242424] text-[14px] font-medium rounded-full py-3.5 hover:bg-[#F7F7F5] transition-colors disabled:opacity-50 flex items-center justify-center gap-2.5">
            <GoogleIcon />
            {loading ? "Connecting…" : "Continue with Google"}
          </button>
          <button onClick={handleLinkedIn} disabled={loading} className="w-full mt-3 bg-[#0A66C2] text-white text-[14px] font-medium rounded-full py-3.5 hover:bg-[#004182] transition-colors disabled:opacity-50 flex items-center justify-center gap-2.5">
            <LinkedInIcon />
            Continue with LinkedIn
          </button>
          {error && <p className="text-[12px] text-[#BE123C] mt-4 text-center">{error}</p>}
        </div>
      </motion.div>
    </div>
  );
}

function GoogleIcon() {
  return <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
    <path fill="#4285F4" d="M21.35 12.27c0-.73-.07-1.43-.2-2.1H12v3.98h5.22a4.46 4.46 0 0 1-1.94 2.93v2.43h3.14c1.84-1.69 2.93-4.18 2.93-7.24Z" />
    <path fill="#34A853" d="M12 21.75c2.63 0 4.84-.87 6.45-2.34l-3.14-2.43c-.87.58-1.98.92-3.31.92-2.54 0-4.7-1.72-5.47-4.04H3.29v2.5A9.74 9.74 0 0 0 12 21.75Z" />
    <path fill="#FBBC05" d="M6.53 13.86a5.86 5.86 0 0 1 0-3.72v-2.5H3.29a9.76 9.76 0 0 0 0 8.72l3.24-2.5Z" />
    <path fill="#EA4335" d="M12 6.1c1.43 0 2.71.49 3.72 1.46l2.79-2.79C16.83 3.2 14.63 2.25 12 2.25a9.74 9.74 0 0 0-8.71 5.39l3.24 2.5C7.3 7.82 9.46 6.1 12 6.1Z" />
  </svg>;
}

function LinkedInIcon() {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    <path d="M20.45 20.45h-3.56v-5.57c0-1.33-.03-3.04-1.85-3.04-1.85 0-2.14 1.45-2.14 2.94v5.67H9.35V9h3.41v1.56h.05c.47-.9 1.63-1.85 3.37-1.85 3.6 0 4.27 2.37 4.27 5.46v6.28ZM5.34 7.43a2.06 2.06 0 1 1 0-4.13 2.06 2.06 0 0 1 0 4.13ZM7.12 20.45H3.56V9h3.56v11.45ZM22.23 0H1.77C.79 0 0 .77 0 1.73v20.54C0 23.23.79 24 1.77 24h20.45c.98 0 1.78-.77 1.78-1.73V1.73C24 .77 23.2 0 22.23 0Z" />
  </svg>;
}
