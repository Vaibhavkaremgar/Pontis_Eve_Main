import React from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { motion } from "framer-motion";
import { loadOnboardingState, saveOnboardingState, clearOnboardingState } from "../lib/onboardingStorage";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

export default function LinkedInAuth() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [loading, setLoading] = React.useState(false);
  const [googleLoading, setGoogleLoading] = React.useState(false);
  const [error, setError] = React.useState(null);

  // Handle backend redirect landing on this page:
  //   returning candidate → /?candidate_id=...
 //   new candidate      → /?linkedin_profile=... (fallback if router sends here)
  React.useEffect(() => {
    const candidateId = searchParams.get("candidate_id");
    const candidateToken = searchParams.get("candidate_token");
    const linkedInProfile = searchParams.get("linkedin_profile");

    if (candidateId) {
      const needsOnboarding = searchParams.get("needs_onboarding") === "true";
      const linkedInProfile = searchParams.get("linkedin_profile");
      if (needsOnboarding) {
        let profile = null;
        if (linkedInProfile) {
          try { profile = JSON.parse(decodeURIComponent(linkedInProfile)); } catch (_) {}
        }
        clearOnboardingState();
        saveOnboardingState({
          linkedInAuthenticated: true,
          candidateId,
          candidateToken,
          linkedInProfile: profile, 
        });
        navigate("/onboarding", { replace: true });
        return;
      }
      saveOnboardingState({
        ...loadOnboardingState(),
        linkedInAuthenticated: true,
        candidateId,
        candidateToken,
      });
      navigate("/dashboard", { replace: true });
      return;
    }

    if (linkedInProfile) {
      try {
        const profile = JSON.parse(decodeURIComponent(linkedInProfile));
        clearOnboardingState();
        saveOnboardingState({
          ...loadOnboardingState(),
          linkedInAuthenticated: true,
          linkedInProfile: profile,
          candidateId: null,
          candidateToken: null,
        });
      } catch (_) {}
      navigate("/onboarding", { replace: true });
      return;
    }

    // Already authenticated from a previous session
    const stored = loadOnboardingState();
    if (stored.linkedInAuthenticated && stored.candidateId) { 
      navigate("/dashboard", { replace: true });
    } else if (stored.linkedInAuthenticated && !stored.candidateId) {
      navigate("/onboarding", { replace: true });
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleLinkedIn = () => {
    setLoading(true); 
    setError(null);
    fetch(`${BACKEND_URL}/api/auth/linkedin/init`)
      .then((r) => r.json())
       .then((data) => {
        sessionStorage.setItem("linkedin_oauth_state", data.state);
        window.location.href = data.auth_url;
      })
      .catch(() => {
        setError("Could not start sign-in. Please try again.");
        setLoading(false);
      });
  };
  const handleGoogle = () => {
    setGoogleLoading(true);
    setError(null);
    fetch(`${BACKEND_URL}/api/auth/google/init`)
      .then((r) => r.json())
      .then((data) => {
        sessionStorage.setItem("google_oauth_state", data.state);
        window.location.href = data.auth_url;
      })
      .catch(() => {
        setError("Could not start Google sign-in. Please try again.");
        setGoogleLoading(false);
      });
  };

  return (
    <div className="min-h-screen bg-[#FBFBF9] text-[#1F1F1F] flex flex-col items-center justify-center px-6">
      <motion.div
        initial={{ opacity: 0, y: 14 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.42, ease: [0.4, 0, 0.2, 1] }}
        className="w-full max-w-md"
      >
        {/* Wordmark */}
        <div className="mb-10 text-center">
          <span className="text-[28px] font-medium tracking-tight text-[#1F1F1F]">Eve</span>
          <p className="text-[13.5px] text-[#9A9A98] mt-1.5 leading-relaxed">
            Your AI-powered career partner.
          </p>
        </div>

        {/* Card */}
        <div className="bg-white border border-black/[0.06] rounded-2xl px-6 py-8 shadow-[0_2px_16px_rgba(0,0,0,0.04)]">
          <h1 className="text-[20px] font-medium tracking-tight leading-tight mb-1.5">
            Get started
          </h1>
          <p className="text-[13px] text-[#9A9A98] mb-7 leading-relaxed">
            Sign in with LinkedIn to build your Living Profile and get matched with the right roles.
          </p>

          <button
            onClick={handleLinkedIn}
            disabled={loading || googleLoading}
            className="w-full bg-[#1F1F1F] text-white text-[14px] font-medium rounded-full py-3.5 hover:bg-black transition-all disabled:bg-black/[0.08] disabled:text-[#B5B5B3] disabled:cursor-not-allowed flex items-center justify-center gap-2.5"
          >
            {loading ? (
              <>
                <span className="w-4 h-4 rounded-full border-2 border-white/30 border-t-white animate-spin" />
                Connecting…
              </>
            ) : (
              <>
                <LinkedInIcon />
                Continue with LinkedIn
              </>
            )}
          </button>

          <div className="flex items-center gap-3 my-4">
            <div className="flex-1 h-px bg-black/[0.06]" />
            <span className="text-[11px] text-[#B5B5B3]">or</span>
            <div className="flex-1 h-px bg-black/[0.06]" />
          </div>

          <button
            onClick={handleGoogle}
            disabled={loading || googleLoading}
            className="w-full bg-white border border-black/[0.10] text-[#1F1F1F] text-[14px] font-medium rounded-full py-3.5 hover:bg-[#F5F5F3] transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2.5"
          >
            {googleLoading ? (
              <>
                <span className="w-4 h-4 rounded-full border-2 border-black/20 border-t-[#1F1F1F] animate-spin" />
                Connecting…
              </>
            ) : (
              <>
                <GoogleIcon />
                Continue with Google
              </>
            )}
          </button>

          {error && (
            <p className="text-[12px] text-[#E11D48] mt-4 text-center leading-relaxed">
              {error}
            </p>
          )}
        </div>

        <p className="text-[11.5px] text-[#B5B5B3] text-center mt-6 leading-relaxed">
          By continuing, you agree to Eve's terms. Your data is kept private.
        </p>
      </motion.div>
    </div>
  );
}

function LinkedInIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 0 1-2.063-2.065 2.064 2.064 0 1 1 2.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z" />
    </svg>
  );
}

function GoogleIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4" />
      <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853" />
      <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z" fill="#FBBC05" />
      <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335" />
    </svg>
  );
}
