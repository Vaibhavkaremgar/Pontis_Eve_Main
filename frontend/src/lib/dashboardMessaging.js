export function buildDashboardEveGreeting({
  firstName,
  profileComplete,
  hasResume,
  voiceIntakeInProgress,
  voiceIntakeResumeQuestion,
}) {
  if (!firstName) return "";

  if (voiceIntakeInProgress && voiceIntakeResumeQuestion) {
    return `Hi ${firstName} - we were in the middle of your intake. ${voiceIntakeResumeQuestion}`;
  }

  return profileComplete
    ? `Hi ${firstName} - great to connect. Your profile looks good. I can help you explore job matches, prep for outreach, or refine any details. What would you like to work on?`
    : `Hi ${firstName} - great to connect. I have your profile in front of me.${
        hasResume ? "" : " Feel free to share any details you'd like to add."
      } What would you like to work on?`;
}

export function buildDashboardEveGreetingFromProfile({
  firstName,
  profileComplete,
  hasResume,
  voiceIntakeResume,
  missingQuestions,
}) {
  // The authoritative missing-question list is built from the persisted
  // profile plus the intake ledger. Prefer it over the resumable voice
  // transcript, whose current_question can be stale after a completed turn.
  const authoritativeQuestion = (missingQuestions || []).find((item) => item?.question)?.question || "";
  const currentQuestion = Array.isArray(missingQuestions)
    ? authoritativeQuestion
    : (voiceIntakeResume?.current_question || voiceIntakeResume?.next_question || "");
  const voiceIntakeInProgress =
    voiceIntakeResume?.status === "in_progress" &&
    Boolean(voiceIntakeResume?.has_open_question) &&
    Boolean(currentQuestion);

  return buildDashboardEveGreeting({
    firstName,
    profileComplete,
    hasResume,
    voiceIntakeInProgress,
    voiceIntakeResumeQuestion: currentQuestion,
  });
}
