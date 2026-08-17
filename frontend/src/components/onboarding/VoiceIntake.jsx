/**
 * VoiceIntake — Step 4 of Eve onboarding.
 * Connects to Vapi, collects candidate voice intake, sends transcript to backend.
 */
import React from "react";
import axios from "axios";
import { motion } from "framer-motion";
import { Mic, MicOff, Radio, PhoneOff, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import useVapi, { VAPI_STATES, buildTranscriptText } from "../../hooks/useVapi";
import { loadOnboardingState, saveOnboardingState } from "../../lib/onboardingStorage";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

const PUBLIC_KEY = process.env.REACT_APP_VAPI_PUBLIC_KEY;
const ASSISTANT_ID = process.env.REACT_APP_EVE_VAPI_ASSISTANT_ID;

/* ---- Visual ring ---- */
function VoiceRing({ state }) {
  const active = state === VAPI_STATES.LISTENING || state === VAPI_STATES.SPEAKING;
  const ringColor = active ? "rgba(45,212,191,0.28)" : "rgba(154,154,152,0.20)";
  const coreGradient = active
    ? "linear-gradient(135deg, #5EEAD4 0%, #2DD4BF 60%, #14B8A6 100%)"
    : "linear-gradient(135deg, #B5B5B3 0%, #9A9A98 100%)";

  return (
    <div className="relative w-44 h-44 flex items-center justify-center">
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="absolute w-32 h-32 rounded-full"
          style={{
            background: ringColor,
            animation: active
              ? `pulse-ring 2.6s cubic-bezier(0.4,0,0.6,1) ${i * 0.85}s infinite`
              : "none",
          }}
        />
      ))}
      <div
        className="relative w-24 h-24 rounded-full flex items-center justify-center shadow-[0_10px_30px_rgba(20,184,166,0.25)]"
        style={{ background: coreGradient }}
        data-testid="voice-visualizer-core"
      >
        {state === VAPI_STATES.SPEAKING ? (
          <Radio className="w-8 h-8 text-white" strokeWidth={1.75} />
        ) : state === VAPI_STATES.CONNECTING ? (
          <div className="w-6 h-6 border-2 border-white border-t-transparent rounded-full animate-spin" />
        ) : (
          <Mic className="w-8 h-8 text-white" strokeWidth={1.75} />
        )}
      </div>
    </div>
  );
}

/* ---- Status label ---- */
const STATE_LABELS = {
  [VAPI_STATES.IDLE]: "Ready to start",
  [VAPI_STATES.CONNECTING]: "Connecting…",
  [VAPI_STATES.LISTENING]: "Listening",
  [VAPI_STATES.SPEAKING]: "Eve is speaking",
  [VAPI_STATES.PROCESSING]: "Processing your intake…",
  [VAPI_STATES.COMPLETED]: "Voice intake complete",
  [VAPI_STATES.ERROR]: "Something went wrong",
};

const NOT_CONFIGURED_MSG = "Voice intake is not configured. Please contact support.";

export default function VoiceIntake({ firstName, candidateId, onComplete, candidateProfile }) {
  const [showTranscript, setShowTranscript] = React.useState(false);
  const [submitting, setSubmitting] = React.useState(false);
  const [retryCount, setRetryCount] = React.useState(0);

  // Persist progress (number of candidate turns answered) so it survives refresh/logout
  const persistProgress = React.useCallback((turns) => {
    const candidateTurns = turns.filter((t) => t.role === "user").length;
    const s = loadOnboardingState();
    if (candidateTurns > (s.voiceIntakeProgress ?? 0)) {
      saveOnboardingState({ ...s, voiceIntakeProgress: candidateTurns });
    }
  }, []);

  // Build assistant overrides with candidate context
  const assistantOverrides = React.useMemo(() => {
    const p = candidateProfile || {};
    const mostRecentExp = Array.isArray(p.experience) && p.experience.length > 0 ? p.experience[0] : null;
    const skillsList = Array.isArray(p.keySkills) && p.keySkills.length > 0
      ? p.keySkills.slice(0, 10).join(", ")
      : (Array.isArray(p.skills) && p.skills.length > 0 ? p.skills.slice(0, 10).join(", ") : "");
    return ({
    variableValues: {
      candidateName: firstName || "there",
      candidateId: candidateId || "",
      "candidate.name": firstName || "there",
      "candidate.number": p.phone || "",
      "resume.years_experience": p.experience_years != null ? String(p.experience_years) : "",
      "resume.most_recent_title": mostRecentExp?.title || p.headline || "",
      "resume.most_recent_company": mostRecentExp?.company || p.current_company || "",
      "resume.skills_list": skillsList,
    },
    metadata: {
      candidateId: candidateId || "",
      source: "eve_candidate_voice_intake",
    },
  });}, [firstName, candidateId, candidateProfile]);

  const { callState, transcript, error, startCall, stopCall, isMuted, toggleMute } = useVapi({
    publicKey: PUBLIC_KEY,
    assistantId: ASSISTANT_ID,
    assistantOverrides,
  });

  // Persist progress whenever transcript grows
  React.useEffect(() => {
    if (transcript.length > 0) persistProgress(transcript);
  }, [transcript, persistProgress]);

  // When Vapi signals processing, submit transcript to backend
  React.useEffect(() => {
    if (callState !== VAPI_STATES.PROCESSING) return;
    if (submitting) return;

    const transcriptText = buildTranscriptText(transcript);
    if (!transcriptText.trim()) {
      // Empty transcript — skip backend call, go to completed
      console.log("[voice-intake] navigating to summary");
      onComplete(null);
      return;
    }

    setSubmitting(true);
    axios
      .post(`${API}/voice/candidate-intake`, {
        transcript: transcriptText,
        voice_notes: transcript.map((t) => ({ role: t.role, text: t.text })),
        candidate_id: candidateId,
      })
      .then((res) => {
        console.log("[voice-intake] navigating to summary");
        onComplete(res.data);
      })
      .catch((err) => {
        console.error("Voice intake submission failed", err);
        toast.error("Couldn't save your voice intake — your profile is still intact.");
        console.log("[voice-intake] navigating to summary");
        // Still advance — don't block onboarding
        onComplete(null);
      })
      .finally(() => setSubmitting(false));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [callState]);

  const handleStartCall = () => {
    console.log("[voice-intake] button clicked");
    startCall();
  };

  const handleRetry = () => {
    console.log("[voice-intake] button clicked");
    setRetryCount((n) => n + 1);
    startCall();
  };

  const isActive =
    callState === VAPI_STATES.LISTENING || callState === VAPI_STATES.SPEAKING;
  const isIdle = callState === VAPI_STATES.IDLE;
  const isError = callState === VAPI_STATES.ERROR;
  const isProcessing =
    callState === VAPI_STATES.PROCESSING || submitting;

  return (
    <div className="flex flex-col items-center gap-8 text-center">
      <VoiceRing state={callState} />

      <div className="space-y-2">
        <h1 className="text-[28px] font-medium tracking-tight">
          {isIdle ? `Hi ${firstName}!` : STATE_LABELS[callState]}
        </h1>
        {isIdle && (
          <p className="text-[13px] text-[#9A9A98] max-w-xs mx-auto leading-relaxed">
            Talk with Eve for 3–5 minutes about what you're looking for. She'll
            enrich your profile as you speak.
          </p>
        )}
        {isError && error && (
          <p className="text-[12px] text-[#E11D48] max-w-xs mx-auto" data-testid="voice-error">
            {error}
          </p>
        )}
      </div>

      {/* Controls */}
      <div className="flex items-center gap-3 flex-wrap justify-center">
        {isIdle && (
          <button
            onClick={handleStartCall}
            data-testid="voice-start-call"
            className="inline-flex items-center gap-2 text-[13px] font-medium px-5 py-2.5 rounded-full bg-[#1F1F1F] text-white hover:bg-black transition-colors"
          >
            <Mic className="w-4 h-4" strokeWidth={1.75} />
            Start voice intake
          </button>
        )}

        {isActive && (
          <button
            onClick={stopCall}
            data-testid="voice-end-call"
            className="inline-flex items-center gap-2 text-[13px] font-medium px-5 py-2.5 rounded-full bg-[#E11D48] text-white hover:bg-red-700 transition-colors"
          >
            <PhoneOff className="w-4 h-4" strokeWidth={1.75} />
            End call
          </button>
        )}

        {isActive && (
          <button
            onClick={toggleMute}
            data-testid="voice-toggle-mute"
            className={`inline-flex items-center gap-1.5 text-[12px] px-3 py-1.5 rounded-full transition-colors font-normal ${
              isMuted
                ? "bg-[#1F1F1F] text-white"
                : "bg-white border border-black/[0.08] text-[#4A4A48] hover:bg-black/[0.03]"
            }`}
          >
            {isMuted ? (
              <MicOff className="w-3.5 h-3.5" strokeWidth={1.75} />
            ) : (
              <Mic className="w-3.5 h-3.5" strokeWidth={1.75} />
            )}
            {isMuted ? "Unmute" : "Mute"}
          </button>
        )}

        {isError && (
          <button
            onClick={handleRetry}
            data-testid="voice-retry"
            className="inline-flex items-center gap-2 text-[13px] font-medium px-5 py-2.5 rounded-full bg-[#1F1F1F] text-white hover:bg-black transition-colors"
          >
            <RotateCcw className="w-4 h-4" strokeWidth={1.75} />
            Retry
          </button>
        )}

        {isActive && (
          <button
            onClick={() => setShowTranscript((s) => !s)}
            data-testid="voice-toggle-transcription"
            className={`inline-flex items-center gap-1.5 text-[12px] px-3 py-1.5 rounded-full transition-colors font-normal ${
              showTranscript
                ? "bg-[#1F1F1F] text-white"
                : "bg-white border border-black/[0.08] text-[#4A4A48] hover:bg-black/[0.03]"
            }`}
          >
            <Radio className="w-3.5 h-3.5" strokeWidth={1.75} />
            Live transcript
          </button>
        )}
      </div>

      {/* Live transcript panel */}
      {showTranscript && transcript.length > 0 && (
        <motion.div
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          className="w-full max-w-sm text-left bg-white border border-black/[0.06] rounded-xl px-4 py-3"
          data-testid="voice-live-transcript"
        >
          <p className="text-[11px] uppercase tracking-wide text-[#9A9A98] font-normal mb-2">
            Live transcript
          </p>
          <div className="space-y-1.5 max-h-40 overflow-y-auto eve-scroll">
            {transcript.slice(-8).map((turn, i) => (
              <p key={i} className="text-[12px] text-[#4A4A48] leading-relaxed">
                <span className="font-medium text-[#1F1F1F]">
                  {turn.role === "assistant" ? "Eve" : "You"}:
                </span>{" "}
                {turn.text}
              </p>
            ))}
          </div>
        </motion.div>
      )}

      {isProcessing && (
        <p className="text-[12px] text-[#9A9A98]" data-testid="voice-processing">
          Saving your intake and updating your profile…
        </p>
      )}
    </div>
  );
}
