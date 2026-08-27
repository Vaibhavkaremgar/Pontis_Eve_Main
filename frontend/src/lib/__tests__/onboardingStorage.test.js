import { isVoiceIntakeCompleteStatus } from "../onboardingStorage";

describe("isVoiceIntakeCompleteStatus", () => {
  it("treats the backend success variants as complete", () => {
    expect(isVoiceIntakeCompleteStatus("completed")).toBe(true);
    expect(isVoiceIntakeCompleteStatus("complete")).toBe(true);
    expect(isVoiceIntakeCompleteStatus("done")).toBe(true);
    expect(isVoiceIntakeCompleteStatus("finished")).toBe(true);
    expect(isVoiceIntakeCompleteStatus("duplicate")).toBe(true);
  });

  it("keeps in-progress and empty statuses incomplete", () => {
    expect(isVoiceIntakeCompleteStatus("in_progress")).toBe(false);
    expect(isVoiceIntakeCompleteStatus("partial")).toBe(false);
    expect(isVoiceIntakeCompleteStatus("")).toBe(false);
    expect(isVoiceIntakeCompleteStatus(null)).toBe(false);
  });
});
