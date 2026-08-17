/**
 * useVapi — reusable Vapi browser lifecycle hook for Eve candidate voice intake.
 *
 * States: idle → connecting → listening → speaking → processing → completed | error
 */
import React from "react";
import Vapi from "@vapi-ai/web";

export const VAPI_STATES = {
  IDLE: "idle",
  CONNECTING: "connecting",
  LISTENING: "listening",
  SPEAKING: "speaking",
  PROCESSING: "processing",
  COMPLETED: "completed",
  ERROR: "error",
};

/**
 * Clean a raw transcript array into a readable string.
 * Deduplicates consecutive identical fragments and trims whitespace.
 */
export function buildTranscriptText(turns) {
  return turns
    .filter((t) => t.text && t.text.trim())
    .map((t) => {
      const speaker = t.role === "assistant" ? "Assistant" : "Candidate";
      return `${speaker}: ${t.text.trim()}`;
    })
    .join("\n");
}

export default function useVapi({ publicKey, assistantId, assistantOverrides }) {
  const vapiRef = React.useRef(null);
  const [callState, setCallState] = React.useState(VAPI_STATES.IDLE);
  const [transcript, setTranscript] = React.useState([]); // { role, text, final }[]
  const [error, setError] = React.useState(null);
  const callIdRef = React.useRef(null);

  // Deduplicate / merge partial transcript turns
  const upsertTurn = React.useCallback((role, text, isFinal) => {
    setTranscript((prev) => {
      const last = prev[prev.length - 1];
      // Replace last partial turn for same speaker
      if (last && last.role === role && !last.final) {
        const updated = [...prev.slice(0, -1), { role, text, final: isFinal }];
        return updated;
      }
      return [...prev, { role, text, final: isFinal }];
    });
  }, []);

  const startCall = React.useCallback(async () => {
    if (!publicKey || !assistantId) {
      setError("Voice intake is not configured. Please contact support.");
      setCallState(VAPI_STATES.ERROR);
      return;
    }

    try {
      setCallState(VAPI_STATES.CONNECTING);
      setError(null);
      setTranscript([]);

      const vapi = new Vapi(publicKey);
      vapiRef.current = vapi;

      vapi.on("call-start", () => {
        callIdRef.current = null;
        console.log("[voice-intake] Vapi call started");
        setCallState(VAPI_STATES.LISTENING);
      });

      vapi.on("speech-start", () => setCallState(VAPI_STATES.SPEAKING));
      vapi.on("speech-end", () => setCallState(VAPI_STATES.LISTENING));

      vapi.on("message", (msg) => {
        if (msg?.type === "transcript") {
          const role = msg.role === "assistant" ? "assistant" : "user";
          const isFinal = msg.transcriptType === "final";
          upsertTurn(role, msg.transcript, isFinal);
        }
        if (msg?.type === "call-update" && msg?.call?.id) {
          callIdRef.current = msg.call.id;
        }
      });

      vapi.on("error", (err) => {
        setError(err?.message || "Voice call error.");
        setCallState(VAPI_STATES.ERROR);
      });

      vapi.on("call-end", () => {
        console.log("[voice-intake] call ended");
        setCallState(VAPI_STATES.PROCESSING);
      });

      console.log("[voice-intake] starting Vapi call");
      await vapi.start(assistantId, assistantOverrides);
    } catch (err) {
      setError(err?.message || "Failed to start voice call.");
      setCallState(VAPI_STATES.ERROR);
    }
  }, [publicKey, assistantId, assistantOverrides, upsertTurn]);

  const stopCall = React.useCallback(() => {
    if (vapiRef.current) {
      try { vapiRef.current.stop(); } catch (_) {}
    }
    setCallState(VAPI_STATES.PROCESSING);
  }, []);

  const [isMuted, setIsMuted] = React.useState(false);

  const toggleMute = React.useCallback(() => {
    if (!vapiRef.current) return;
    try {
      const next = !isMuted;
      vapiRef.current.setMuted(next);
      setIsMuted(next);
    } catch (_) {}
  }, [isMuted]);

  // Cleanup on unmount
  React.useEffect(() => {
    return () => {
      if (vapiRef.current) {
        try { vapiRef.current.stop(); } catch (_) {}
      }
    };
  }, []);

  return {
    callState,
    transcript,
    error,
    callId: callIdRef,
    startCall,
    stopCall,
    isMuted,
    toggleMute,
  };
}
