import { buildDashboardEveGreeting } from "../../lib/dashboardMessaging";

describe("Dashboard voice intake resume", () => {
  it("shows the saved unanswered voice intake question instead of the generic greeting", () => {
    const currentQuestion =
      "Are there specific industries or types of companies you'd prefer to work with in your next role?";

    const greeting = buildDashboardEveGreeting({
      firstName: "Suram",
      profileComplete: true,
      hasResume: true,
      voiceIntakeInProgress: true,
      voiceIntakeResumeQuestion: currentQuestion,
    });

    expect(greeting).toContain(currentQuestion);
    expect(greeting).not.toContain("Your profile looks good");
  });
});
