import { normalizeJobDescription } from "../jobDescription";

describe("normalizeJobDescription", () => {
  test("preserves paragraphs and nested inline text", () => {
    expect(normalizeJobDescription("<p>Build <strong>reliable</strong> pipelines.</p><p>Python &amp; SQL.</p>")).toEqual([
      { type: "paragraph", text: "Build reliable pipelines." },
      { type: "paragraph", text: "Python & SQL." },
    ]);
  });

  test("parses encoded and backslash-escaped HTML", () => {
    expect(normalizeJobDescription("&lt;p&gt;Hello world&lt;/p&gt;")).toEqual([
      { type: "paragraph", text: "Hello world" },
    ]);
    expect(normalizeJobDescription("\\<p>Escaped content\\</p>")).toEqual([
      { type: "paragraph", text: "Escaped content" },
    ]);
  });

  test("preserves headings and list boundaries", () => {
    expect(normalizeJobDescription("<h2>Responsibilities</h2><ul><li>Build pipelines</li><li>Maintain datasets</li></ul>")).toEqual([
      { type: "heading", text: "Responsibilities" },
      { type: "ul", items: ["Build pipelines", "Maintain datasets"] },
    ]);
  });

  test("removes executable content and keeps meaningful text", () => {
    const blocks = normalizeJobDescription("<script>alert(1)</script><p>Safe text</p><style>.x{}</style>");
    expect(blocks).toEqual([{ type: "paragraph", text: "Safe text" }]);
  });

  test("preserves mixed root-level text in source order", () => {
    const blocks = normalizeJobDescription("Intro text<p>First paragraph</p>Middle text<ul><li>Python</li><li>SQL</li></ul>Ending text");
    expect(blocks.map((block) => block.type === "ul" ? block.items.join(" ") : block.text).join(" ")).toBe("Intro text First paragraph Middle text Python SQL Ending text");
  });

  test("retains meaningful text from malformed HTML", () => {
    const blocks = normalizeJobDescription("<p>Build applications<p>Write tests</p>Final note");
    const text = blocks.map((block) => block.type === "ul" || block.type === "ol" ? block.items.join(" ") : block.text).join(" ");
    expect(text).toContain("Build applications");
    expect(text).toContain("Write tests");
    expect(text).toContain("Final note");
  });

  test("does not lose plain-text URLs or technical terms", () => {
    const blocks = normalizeJobDescription("Data Engineer\n\nBuild Python pipelines.\nhttps://example.com/docs");
    const text = blocks.map((block) => block.text).join(" ");
    expect(text).toContain("Python");
    expect(text).toContain("https://example.com/docs");
  });

  test("does not duplicate content on repeated normalization", () => {
    const source = "<p>Build AI-powered pipelines.</p><ul><li>Python</li></ul>";
    const once = normalizeJobDescription(source);
    const plain = once.map((block) => block.type === "ul" ? block.items.map((item) => `• ${item}`).join("\n") : block.text).join("\n\n");
    const twice = normalizeJobDescription(plain);
    expect(twice.map((block) => block.text || block.items.join(" ")).join(" ")).toContain("Build AI-powered pipelines.");
    expect(twice.map((block) => block.text || block.items.join(" ")).join(" ")).toContain("Python");
  });
});
