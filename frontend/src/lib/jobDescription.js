import DOMPurify from "dompurify";

const BLOCK_TAGS = new Set(["P", "DIV", "SECTION", "ARTICLE", "HEADER", "BR"]);
const HEADING_TAGS = new Set(["H1", "H2", "H3", "H4", "H5", "H6"]);

function cleanWhitespace(value) {
  return String(value || "")
    .replace(/[\u200B\u200C\u200D\uFEFF]/g, "")
    .replace(/\u00A0/g, " ")
    .replace(/[ \t]+/g, " ")
    .replace(/[ \t]*\n[ \t]*/g, "\n")
    .trim();
}

function decodePlainText(value) {
  const template = document.createElement("template");
  template.innerHTML = String(value || "");
  return cleanWhitespace(template.content.textContent || "");
}

function hasMarkup(value) {
  return /<\/?[a-z][^>]*>/i.test(String(value || ""));
}

export function normalizeJobDescription(source) {
  if (!source || typeof source !== "string") return [];
  const input = source.trim();
  if (!input) return [];
  if (!hasMarkup(input)) {
    return input.split(/\n{2,}/).map((text) => ({ type: "paragraph", text: cleanWhitespace(decodePlainText(text)) })).filter((block) => block.text);
  }

  const safe = DOMPurify.sanitize(input, {
    ALLOWED_TAGS: ["p", "div", "section", "article", "br", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "strong", "b", "em", "i", "a"],
    ALLOWED_ATTR: ["href", "title"],
    FORBID_ATTR: ["style", "class", "id", "onclick", "onerror"],
  });
  const doc = new DOMParser().parseFromString(`<div>${safe}</div>`, "text/html");
  const root = doc.body.firstElementChild;
  const blocks = [];

  Array.from(root?.children || []).forEach((node) => {
    const tag = node.tagName;
    if (HEADING_TAGS.has(tag)) {
      const text = cleanWhitespace(node.textContent);
      if (text) blocks.push({ type: "heading", text });
    } else if (tag === "UL" || tag === "OL") {
      const items = Array.from(node.querySelectorAll(":scope > li"))
        .map((item) => cleanWhitespace(item.textContent))
        .filter(Boolean);
      if (items.length) blocks.push({ type: tag === "OL" ? "ol" : "ul", items });
    } else if (BLOCK_TAGS.has(tag) || node.textContent?.trim()) {
      const text = cleanWhitespace(node.textContent);
      if (text) blocks.push({ type: "paragraph", text });
    }
  });

  if (!blocks.length) {
    const text = cleanWhitespace(root?.textContent || input);
    if (text) blocks.push({ type: "paragraph", text });
  }
  return blocks;
}

export function normalizeJobDescriptionText(source) {
  return normalizeJobDescription(source)
    .map((block) => block.type === "heading" ? `${block.text}:` : block.type === "paragraph" ? block.text : block.items.map((item) => `• ${item}`).join("\n"))
    .join("\n\n");
}
