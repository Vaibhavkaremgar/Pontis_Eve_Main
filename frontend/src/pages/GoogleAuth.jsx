import React from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { motion } from "framer-motion";
import { loadOnboardingState, saveOnboardingState, clearOnboardingState } from "../lib/onboardingStorage";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

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
        const state = { ...loadOnboardingState(), authenticated: true,
          candidateId: session.candidate_id || null, candidateToken: session.candidate_token || null,
          profile: session.profile || session.linkedin_profile || null };
        if (session.needs_onboarding || !session.candidate_id) {
          clearOnboardingState(); saveOnboardingState(state); navigate("/onboarding", { replace: true });
        } else { saveOnboardingState(state); navigate("/dashboard", { replace: true }); }
      }).catch(() => setError("Sign-in could not be completed. Please try again."));
      return;
    }
    const stored = loadOnboardingState();
    if (stored.authenticated && stored.candidateId) navigate("/dashboard", { replace: true });
    else if (stored.authenticated) navigate("/onboarding", { replace: true });
  }, [navigate, searchParams]);

  const handleGoogle = () => { setLoading(true); setError(null); window.location.assign(`${BACKEND_URL}/api/auth/google/init?redirect=true`); };
  const handleLinkedIn = () => { setLoading(true); setError(null); window.location.assign(`${BACKEND_URL}/api/auth/linkedin/login`); };
  return <div className="min-h-screen bg-[#FBFBF9] text-[#1F1F1F] flex flex-col items-center justify-center px-6"><motion.div initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} className="w-full max-w-md"><div className="mb-10 text-center"><span className="text-[28px] font-medium tracking-tight">Eve</span><p className="text-[13.5px] text-[#9A9A98] mt-1.5">Your AI-powered career partner.</p></div><div className="bg-white border border-black/[0.06] rounded-2xl px-6 py-8 shadow-sm"><h1 className="text-[20px] font-medium mb-1.5">Get started</h1><p className="text-[13px] text-[#9A9A98] mb-7">Sign in to build your Living Profile and get matched with the right roles.</p><button onClick={handleGoogle} disabled={loading} className="w-full bg-white border border-black/[0.10] text-[#1F1F1F] text-[14px] font-medium rounded-full py-3.5 hover:bg-[#F5F5F3] disabled:opacity-50">{loading ? "Connecting…" : "Continue with Google"}</button><button onClick={handleLinkedIn} disabled={loading} className="w-full mt-3 bg-[#0A66C2] text-white text-[14px] font-medium rounded-full py-3.5 hover:bg-[#004182] disabled:opacity-50">Continue with LinkedIn</button>{error && <p className="text-[12px] text-[#E11D48] mt-4 text-center">{error}</p>}</div></motion.div></div>;
}
