import { getDynamicChatSuggestions } from "../chatSuggestions";

describe("getDynamicChatSuggestions", () => {
  it("does not suggest availability or salary after a combined answer", () => {
    const suggestions = getDynamicChatSuggestions({
      availability: "Immediately",
      salary_expectation: "7–8 LPA",
    }, 10);

    expect(suggestions).not.toContain("What's your availability to start?");
    expect(suggestions).not.toContain("What salary range are you targeting?");
  });

  it("treats raw profile fields as completed topics", () => {
    const suggestions = getDynamicChatSuggestions({
      raw_data: {
        preferred_roles: ["Product Manager"],
        skills: ["SQL"],
        notice_period: "30 days",
        additional_information: "Expected compensation: 12 LPA",
      },
    }, 10);

    expect(suggestions).not.toContain("What roles are you targeting?");
    expect(suggestions).not.toContain("What are your top skills?");
    expect(suggestions).not.toContain("What's your availability to start?");
    expect(suggestions).not.toContain("What salary range are you targeting?");
  });
});
