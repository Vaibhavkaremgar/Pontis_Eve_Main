import { isVoiceIntakeCompleteStatus } from "./onboardingStorage";

export function getVoiceIntakeCenterView(voiceIntakeResume) {
  const currentQuestion = voiceIntakeResume?.current_question || "";
  const isInProgress =
    voiceIntakeResume?.status === "in_progress" &&
    Boolean(voiceIntakeResume?.has_open_question) &&
    Boolean(currentQuestion);

  if (isInProgress) return "chat";
  if (isVoiceIntakeCompleteStatus(voiceIntakeResume?.status)) return "swipe";
  return null;
}
